"""End-to-end tests for the subject-service API.

Covers spec 0004's acceptance criteria:
    * ``POST /v1/services`` → 201, response includes the persisted shape.
    * ``GET  /v1/services/{id}`` → 200 after registration.
    * Tenant isolation: tenant A can't read tenant B's service.
    * 409 on second registration (one-per-tenant rule).
    * No Prometheus integration → ``validation_pending=True`` in the
      response.
    * Prometheus mocked via respx → label_selector validation runs and
      affects warnings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_sre.api.deps import _get_envelope_crypto
from ai_sre.config import get_settings
from ai_sre.core.integration.repository import IntegrationRepository
from ai_sre.core.integration.service import IntegrationService
from ai_sre.core.tenant.api_key_service import ApiKeyService
from ai_sre.core.tenant.repository import ApiKeyRepository, TenantRepository
from ai_sre.core.tenant.service import TenantService
from ai_sre.db import session_scope
from ai_sre.utils.crypto import random_base64_key


@pytest.fixture(autouse=True)
def _valid_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a real 32-byte encryption key and clear cached singletons.

    The default ``AI_SRE_TENANT_ENCRYPTION_KEY`` in ``.env.example`` only
    decodes to 24 bytes, but ``EnvelopeEncryptionService`` requires 32.
    """
    monkeypatch.setenv("AI_SRE_TENANT_ENCRYPTION_KEY", random_base64_key())
    get_settings.cache_clear()
    _get_envelope_crypto.cache_clear()


@dataclass(frozen=True)
class _Bootstrap:
    tenant_id: UUID
    api_key: str
    headers: dict[str, str]


async def _bootstrap_tenant(*, slug: str) -> _Bootstrap:
    """Insert a tenant + issue an API key via the service layer.

    spec 0001's ``POST /v1/tenant`` doesn't return a bootstrap key (flagged
    as a follow-up), so we go through the service layer directly.
    """
    async with session_scope() as session:
        tenants = TenantService(TenantRepository(session))
        tenant = await tenants.create(name=slug.capitalize(), slug=slug)
        keys = ApiKeyService(ApiKeyRepository(session), TenantRepository(session))
        issued = await keys.issue(tenant.id, name="bootstrap")
    return _Bootstrap(
        tenant_id=tenant.id,
        api_key=issued.key,
        headers={"Authorization": f"Bearer {issued.key}"},
    )


async def _create_prom_integration(
    tenant_id: UUID, *, url: str = "http://prom-fake.example.com"
) -> UUID:
    """Insert a Prometheus integration directly via the service layer."""
    async with session_scope() as session:
        crypto = _get_envelope_crypto()
        service = IntegrationService(
            IntegrationRepository(session, tenant_id), crypto
        )
        row = await service.create(
            kind="prometheus",
            name="prod",
            config={"url": url, "auth": {"type": "none"}},
        )
    return row.id


@pytest_asyncio.fixture
async def bootstrap(db_engine: AsyncEngine) -> _Bootstrap:
    return await _bootstrap_tenant(slug="acme")


@pytest_asyncio.fixture
async def other_bootstrap(db_engine: AsyncEngine) -> _Bootstrap:
    return await _bootstrap_tenant(slug="other")


def _payload(name: str = "checkout", **labels: str) -> dict[str, Any]:
    return {
        "name": name,
        "label_selector": labels or {"app": "checkout", "env": "prod"},
    }


def _instant_response_n_series(n: int) -> dict[str, Any]:
    """Build a Prometheus instant-query response with ``n`` matching series."""
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {"metric": {"__name__": "up", "i": str(i)}, "value": [1700000000, "1"]}
                for i in range(n)
            ],
        },
    }


# ---- No Prometheus integration → validation_pending ----


@pytest.mark.integration
@pytest.mark.asyncio
async def test_register_without_prometheus_marks_validation_pending(
    client: AsyncClient, bootstrap: _Bootstrap
) -> None:
    resp = await client.post(
        "/v1/services", headers=bootstrap.headers, json=_payload()
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["validation_pending"] is True
    assert body["warnings"] == []
    assert body["name"] == "checkout"
    assert body["label_selector"] == {"app": "checkout", "env": "prod"}


# ---- POST → GET roundtrip ----


@pytest.mark.integration
@pytest.mark.asyncio
async def test_register_then_get_roundtrip(
    client: AsyncClient, bootstrap: _Bootstrap
) -> None:
    resp = await client.post(
        "/v1/services", headers=bootstrap.headers, json=_payload()
    )
    assert resp.status_code == 201
    created = resp.json()
    service_id = created["id"]

    resp = await client.get(
        f"/v1/services/{service_id}", headers=bootstrap.headers
    )
    assert resp.status_code == 200
    fetched = resp.json()
    assert fetched["id"] == service_id
    # warnings is registration-time only; GET always returns [].
    assert fetched["warnings"] == []


# ---- Duplicate registration → 409 ----


@pytest.mark.integration
@pytest.mark.asyncio
async def test_register_twice_returns_409(
    client: AsyncClient, bootstrap: _Bootstrap
) -> None:
    resp = await client.post(
        "/v1/services", headers=bootstrap.headers, json=_payload()
    )
    assert resp.status_code == 201

    resp = await client.post(
        "/v1/services",
        headers=bootstrap.headers,
        json=_payload(name="duplicate"),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "service.already_exists"


# ---- Tenant isolation ----


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tenant_isolation(
    client: AsyncClient,
    bootstrap: _Bootstrap,
    other_bootstrap: _Bootstrap,
) -> None:
    """Tenant A's service is invisible to tenant B."""
    resp = await client.post(
        "/v1/services", headers=bootstrap.headers, json=_payload()
    )
    assert resp.status_code == 201
    a_id = resp.json()["id"]

    # Tenant B can't read A's service by id.
    resp = await client.get(
        f"/v1/services/{a_id}", headers=other_bootstrap.headers
    )
    assert resp.status_code == 404

    # And tenant B can still register their own — no shared state.
    resp = await client.post(
        "/v1/services", headers=other_bootstrap.headers, json=_payload(name="other-svc")
    )
    assert resp.status_code == 201


# ---- Selector matched zero series ----


@pytest.mark.integration
@pytest.mark.asyncio
@respx.mock
async def test_register_with_prometheus_zero_series_warns(
    client: AsyncClient, bootstrap: _Bootstrap
) -> None:
    """Prom present + selector matches nothing → 201 + warning, no
    validation_pending (Prometheus did answer, the answer was 'no')."""
    url = "http://prom-empty.example.com"
    respx.get(f"{url}/api/v1/query").mock(
        return_value=httpx.Response(200, json=_instant_response_n_series(0))
    )
    await _create_prom_integration(bootstrap.tenant_id, url=url)

    resp = await client.post(
        "/v1/services", headers=bootstrap.headers, json=_payload()
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["validation_pending"] is False
    assert len(body["warnings"]) == 1
    assert "0 series" in body["warnings"][0]


# ---- Selector matched N series ----


@pytest.mark.integration
@pytest.mark.asyncio
@respx.mock
async def test_register_with_prometheus_matching_series_no_warnings(
    client: AsyncClient, bootstrap: _Bootstrap
) -> None:
    url = "http://prom-healthy.example.com"
    respx.get(f"{url}/api/v1/query").mock(
        return_value=httpx.Response(200, json=_instant_response_n_series(3))
    )
    await _create_prom_integration(bootstrap.tenant_id, url=url)

    resp = await client.post(
        "/v1/services", headers=bootstrap.headers, json=_payload()
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["validation_pending"] is False
    assert body["warnings"] == []


# ---- Invalid payloads ----


@pytest.mark.integration
@pytest.mark.asyncio
async def test_empty_label_selector_returns_422(
    client: AsyncClient, bootstrap: _Bootstrap
) -> None:
    resp = await client.post(
        "/v1/services",
        headers=bootstrap.headers,
        json={"name": "x", "label_selector": {}},
    )
    assert resp.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(client: AsyncClient) -> None:
    resp = await client.post("/v1/services", json=_payload())
    # 401 (no Bearer) or 422 (Authorization header missing) — FastAPI's
    # Header(...) returns the latter when the header is absent entirely.
    assert resp.status_code in (401, 422)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unknown_service_id_returns_404(
    client: AsyncClient, bootstrap: _Bootstrap
) -> None:
    from ai_sre.utils.ids import new_id

    resp = await client.get(
        f"/v1/services/{new_id()}", headers=bootstrap.headers
    )
    assert resp.status_code == 404
