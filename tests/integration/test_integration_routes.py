"""End-to-end tests for the integrations API.

Covers spec 0002's integration acceptance criteria:
    * Full POST → GET → DELETE flow with API key auth.
    * Tenant isolation: tenant A cannot see, fetch, or delete tenant B's
      integrations.
    * Duplicate ``(kind, name)`` returns 409.
    * Invalid payloads return 422 (FastAPI's automatic Pydantic validation
      against the discriminated auth schema).
    * The encrypted blob and tokens never leak in any API response.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_sre.api.deps import _get_envelope_crypto
from ai_sre.config import get_settings
from ai_sre.core.tenant.api_key_service import ApiKeyService
from ai_sre.core.tenant.repository import ApiKeyRepository, TenantRepository
from ai_sre.core.tenant.service import TenantService
from ai_sre.db import session_scope
from ai_sre.utils.crypto import random_base64_key


@pytest.fixture(autouse=True)
def _valid_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default ``AI_SRE_TENANT_ENCRYPTION_KEY`` in ``.env.example`` only
    decodes to 24 bytes, but ``EnvelopeEncryptionService`` requires 32.
    Inject a real 32-byte key and clear the relevant caches so each test
    gets a fresh crypto service.
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
    """Insert a tenant and issue an API key directly via the service layer.

    spec 0001's ``POST /v1/tenant`` doesn't return a bootstrap key (flagged
    as a follow-up), so we use the service layer to create the first key.
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


@pytest_asyncio.fixture
async def bootstrap(db_engine: AsyncEngine) -> _Bootstrap:
    """Create tenant ``acme`` and issue an API key for it.

    Depends on ``db_engine`` so the schema is built and the app's cached
    engine/sessionmaker are reset before we touch them via ``session_scope``.
    """
    return await _bootstrap_tenant(slug="acme")


@pytest_asyncio.fixture
async def other_bootstrap(db_engine: AsyncEngine) -> _Bootstrap:
    """Second tenant for cross-tenant isolation tests."""
    return await _bootstrap_tenant(slug="other")


def _prom_payload(
    name: str = "prod", token: str = "s3cr3t-token-value"
) -> dict[str, Any]:
    return {
        "kind": "prometheus",
        "name": name,
        "config": {
            "url": "https://prom.example.com",
            "auth": {"type": "bearer", "token": token},
        },
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_create_get_list_delete_flow(
    client: AsyncClient, bootstrap: _Bootstrap
) -> None:
    # 1. POST creates the integration.
    resp = await client.post(
        "/v1/integrations", headers=bootstrap.headers, json=_prom_payload()
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["kind"] == "prometheus"
    assert created["name"] == "prod"
    assert created["status"] == "pending"
    assert created["config_public"] == {
        "url_host": "prom.example.com",
        "auth_type": "bearer",
    }
    # Secrets never appear in the response.
    body_text = resp.text
    assert "s3cr3t-token-value" not in body_text
    assert "config_encrypted" not in created

    integration_id = created["id"]

    # 2. GET single returns the same row.
    resp = await client.get(
        f"/v1/integrations/{integration_id}", headers=bootstrap.headers
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == integration_id

    # 3. LIST returns it.
    resp = await client.get("/v1/integrations", headers=bootstrap.headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["id"] == integration_id

    # 4. DELETE removes it, then GET is 404.
    resp = await client.delete(
        f"/v1/integrations/{integration_id}", headers=bootstrap.headers
    )
    assert resp.status_code == 204
    resp = await client.get(
        f"/v1/integrations/{integration_id}", headers=bootstrap.headers
    )
    assert resp.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_duplicate_returns_409(
    client: AsyncClient, bootstrap: _Bootstrap
) -> None:
    payload = _prom_payload()
    resp = await client.post("/v1/integrations", headers=bootstrap.headers, json=payload)
    assert resp.status_code == 201

    resp = await client.post("/v1/integrations", headers=bootstrap.headers, json=payload)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "integration.already_exists"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unknown_kind_returns_422(
    client: AsyncClient, bootstrap: _Bootstrap
) -> None:
    resp = await client.post(
        "/v1/integrations",
        headers=bootstrap.headers,
        json={"kind": "loki", "name": "x", "config": {"url": "http://x"}},
    )
    assert resp.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_invalid_auth_payload_returns_422(
    client: AsyncClient, bootstrap: _Bootstrap
) -> None:
    """type=bearer with no token must fail the discriminated-union check."""
    resp = await client.post(
        "/v1/integrations",
        headers=bootstrap.headers,
        json={
            "kind": "prometheus",
            "name": "prod",
            "config": {"url": "http://x", "auth": {"type": "bearer"}},  # missing token
        },
    )
    assert resp.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tenant_isolation(
    client: AsyncClient, bootstrap: _Bootstrap, other_bootstrap: _Bootstrap
) -> None:
    """Tenant A's integrations must be invisible to tenant B."""
    # Tenant A creates one.
    resp = await client.post(
        "/v1/integrations", headers=bootstrap.headers, json=_prom_payload()
    )
    assert resp.status_code == 201
    a_id = resp.json()["id"]

    # Tenant B can't see it in their list.
    resp = await client.get("/v1/integrations", headers=other_bootstrap.headers)
    assert resp.status_code == 200
    assert resp.json() == []

    # Tenant B can't read it by id.
    resp = await client.get(f"/v1/integrations/{a_id}", headers=other_bootstrap.headers)
    assert resp.status_code == 404

    # Tenant B can't delete it either.
    resp = await client.delete(
        f"/v1/integrations/{a_id}", headers=other_bootstrap.headers
    )
    assert resp.status_code == 404

    # And A's still there.
    resp = await client.get(f"/v1/integrations/{a_id}", headers=bootstrap.headers)
    assert resp.status_code == 200


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(client: AsyncClient) -> None:
    resp = await client.get("/v1/integrations")
    assert resp.status_code in (401, 422)

# The health-check endpoint was a 501 stub in spec 0002 and went live in
# spec 0003. Its current behaviour (202 with a fake queue, full worker-body
# flow) is covered end-to-end in tests/integration/test_integration_health_check.py.
