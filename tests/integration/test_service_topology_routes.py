"""End-to-end tests for topology + metric-catalog discovery (spec 0005).

Covers:
    * Worker bodies (run_topology_refresh / run_metric_catalog_refresh)
      against a respx-mocked Prometheus persist rows; the read routes then
      return them.
    * GET /topology, PATCH /topology (confirm edges), GET /metrics (+filter).
    * Refresh routes enqueue (202) and 404 for unknown / cross-tenant ids.
    * Registration enqueues an immediate refresh of both.
    * The periodic fan-out helper enumerates (tenant, service) pairs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_sre.api.deps import _get_envelope_crypto, get_job_queue
from ai_sre.config import get_settings
from ai_sre.core.integration.repository import IntegrationRepository
from ai_sre.core.integration.service import IntegrationService
from ai_sre.core.service.repository import ServiceRepository
from ai_sre.core.tenant.api_key_service import ApiKeyService
from ai_sre.core.tenant.repository import ApiKeyRepository, TenantRepository
from ai_sre.core.tenant.service import TenantService
from ai_sre.db import session_scope
from ai_sre.queue.base import JobId, JobQueue
from ai_sre.utils.crypto import random_base64_key
from ai_sre.workers.app import (
    list_services_for_refresh,
    run_metric_catalog_refresh,
    run_topology_refresh,
)


@dataclass(frozen=True)
class _Bootstrap:
    tenant_id: UUID
    api_key: str
    headers: dict[str, str]


@pytest.fixture(autouse=True)
def _valid_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SRE_TENANT_ENCRYPTION_KEY", random_base64_key())
    get_settings.cache_clear()
    _get_envelope_crypto.cache_clear()


async def _bootstrap_tenant(*, slug: str) -> _Bootstrap:
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


async def _create_prom_integration(tenant_id: UUID, *, url: str) -> UUID:
    async with session_scope() as session:
        service = IntegrationService(
            IntegrationRepository(session, tenant_id), _get_envelope_crypto()
        )
        row = await service.create(
            kind="prometheus", name="prod", config={"url": url, "auth": {"type": "none"}}
        )
    return row.id


async def _create_service(
    tenant_id: UUID, *, label_selector: dict[str, str] | None = None
) -> UUID:
    """Insert a service row directly (bypassing ServiceService validation,
    which is covered in test_service_routes)."""
    async with session_scope() as session:
        repo = ServiceRepository(session, tenant_id)
        row = await repo.create(
            name="checkout",
            label_selector=label_selector or {"app": "checkout"},
            ownership=None,
            slo_config=None,
        )
    return row.id


def _series(data: list[dict[str, str]]) -> dict[str, Any]:
    return {"status": "success", "data": data}


def _metadata(data: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    return {"status": "success", "data": data}


class _CapturingJobQueue(JobQueue):
    """In-memory queue that records every enqueue."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def enqueue(
        self, kind: str, payload: dict[str, Any], *, delay_seconds: int = 0
    ) -> JobId:
        self.calls.append((kind, payload))
        return f"fake-job-{len(self.calls)}"

    async def cancel(self, job_id: JobId) -> None:
        return None


@pytest_asyncio.fixture
async def bootstrap(db_engine: AsyncEngine) -> _Bootstrap:
    return await _bootstrap_tenant(slug="acme")


@pytest_asyncio.fixture
async def client_with_fake_queue(
    db_engine: AsyncEngine,
) -> AsyncIterator[tuple[AsyncClient, _CapturingJobQueue]]:
    from ai_sre.main import create_app

    app = create_app()
    queue = _CapturingJobQueue()
    app.dependency_overrides[get_job_queue] = lambda: queue
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, queue


# ---- Topology: worker body → GET ----


@respx.mock
@pytest.mark.integration
@pytest.mark.asyncio
async def test_topology_refresh_worker_populates_graph(
    client: AsyncClient, bootstrap: _Bootstrap
) -> None:
    url = "http://prom-topo.example.com"
    respx.get(f"{url}/api/v1/series").mock(
        return_value=httpx.Response(
            200,
            json=_series(
                [
                    {"__name__": "requests_total", "app": "checkout", "dst": "payments"},
                    {"__name__": "requests_total", "app": "checkout", "upstream": "frontend"},
                ]
            ),
        )
    )
    await _create_prom_integration(bootstrap.tenant_id, url=url)
    service_id = await _create_service(bootstrap.tenant_id)

    await run_topology_refresh(str(bootstrap.tenant_id), str(service_id))

    resp = await client.get(
        f"/v1/services/{service_id}/topology", headers=bootstrap.headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {d["name"] for d in body["downstream"]} == {"payments"}
    assert {d["name"] for d in body["upstream"]} == {"frontend"}
    assert all(d["confirmed_by_user"] is False for d in body["downstream"])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_topology_empty_for_fresh_service(
    client: AsyncClient, bootstrap: _Bootstrap
) -> None:
    service_id = await _create_service(bootstrap.tenant_id)
    resp = await client.get(
        f"/v1/services/{service_id}/topology", headers=bootstrap.headers
    )
    assert resp.status_code == 200
    assert resp.json() == {"upstream": [], "downstream": []}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_topology_unknown_service_404(
    client: AsyncClient, bootstrap: _Bootstrap
) -> None:
    from ai_sre.utils.ids import new_id

    resp = await client.get(
        f"/v1/services/{new_id()}/topology", headers=bootstrap.headers
    )
    assert resp.status_code == 404


@respx.mock
@pytest.mark.integration
@pytest.mark.asyncio
async def test_patch_topology_confirms_edge(
    client: AsyncClient, bootstrap: _Bootstrap
) -> None:
    url = "http://prom-confirm.example.com"
    respx.get(f"{url}/api/v1/series").mock(
        return_value=httpx.Response(
            200,
            json=_series([{"__name__": "x", "app": "checkout", "dst": "payments"}]),
        )
    )
    await _create_prom_integration(bootstrap.tenant_id, url=url)
    service_id = await _create_service(bootstrap.tenant_id)
    await run_topology_refresh(str(bootstrap.tenant_id), str(service_id))

    resp = await client.get(
        f"/v1/services/{service_id}/topology", headers=bootstrap.headers
    )
    dep_id = resp.json()["downstream"][0]["id"]

    resp = await client.patch(
        f"/v1/services/{service_id}/topology",
        headers=bootstrap.headers,
        json={"edits": [{"dependency_id": dep_id, "confirmed_by_user": True}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["downstream"][0]["confirmed_by_user"] is True


@respx.mock
@pytest.mark.integration
@pytest.mark.asyncio
async def test_topology_refresh_is_idempotent_and_preserves_confirmation(
    client: AsyncClient, bootstrap: _Bootstrap
) -> None:
    """A second refresh upserts (no duplicates) and keeps confirmed_by_user."""
    url = "http://prom-idem.example.com"
    respx.get(f"{url}/api/v1/series").mock(
        return_value=httpx.Response(
            200,
            json=_series([{"__name__": "x", "app": "checkout", "dst": "payments"}]),
        )
    )
    await _create_prom_integration(bootstrap.tenant_id, url=url)
    service_id = await _create_service(bootstrap.tenant_id)

    await run_topology_refresh(str(bootstrap.tenant_id), str(service_id))
    dep_id = (
        await client.get(
            f"/v1/services/{service_id}/topology", headers=bootstrap.headers
        )
    ).json()["downstream"][0]["id"]
    await client.patch(
        f"/v1/services/{service_id}/topology",
        headers=bootstrap.headers,
        json={"edits": [{"dependency_id": dep_id, "confirmed_by_user": True}]},
    )

    # Second refresh: same edge.
    await run_topology_refresh(str(bootstrap.tenant_id), str(service_id))

    body = (
        await client.get(
            f"/v1/services/{service_id}/topology", headers=bootstrap.headers
        )
    ).json()
    assert len(body["downstream"]) == 1  # no duplicate
    assert body["downstream"][0]["confirmed_by_user"] is True  # preserved


# ---- Metric catalog: worker body → GET ----


@respx.mock
@pytest.mark.integration
@pytest.mark.asyncio
async def test_metric_catalog_refresh_worker_and_list(
    client: AsyncClient, bootstrap: _Bootstrap
) -> None:
    url = "http://prom-metrics.example.com"
    respx.get(f"{url}/api/v1/series").mock(
        return_value=httpx.Response(
            200,
            json=_series(
                [
                    {"__name__": "http_requests_total", "app": "checkout", "method": "GET"},
                    {"__name__": "latency_seconds", "app": "checkout"},
                ]
            ),
        )
    )
    respx.get(f"{url}/api/v1/metadata").mock(
        return_value=httpx.Response(
            200,
            json=_metadata(
                {"http_requests_total": [{"type": "counter", "help": "reqs", "unit": ""}]}
            ),
        )
    )
    await _create_prom_integration(bootstrap.tenant_id, url=url)
    service_id = await _create_service(bootstrap.tenant_id)

    await run_metric_catalog_refresh(str(bootstrap.tenant_id), str(service_id))

    resp = await client.get(
        f"/v1/services/{service_id}/metrics", headers=bootstrap.headers
    )
    assert resp.status_code == 200, resp.text
    names = [m["metric_name"] for m in resp.json()]
    assert names == ["http_requests_total", "latency_seconds"]

    # Substring filter.
    resp = await client.get(
        f"/v1/services/{service_id}/metrics?filter=latency", headers=bootstrap.headers
    )
    assert [m["metric_name"] for m in resp.json()] == ["latency_seconds"]


# ---- Refresh routes enqueue ----


@pytest.mark.integration
@pytest.mark.asyncio
async def test_refresh_routes_enqueue_jobs(
    client_with_fake_queue: tuple[AsyncClient, _CapturingJobQueue],
    bootstrap: _Bootstrap,
) -> None:
    client, queue = client_with_fake_queue
    service_id = await _create_service(bootstrap.tenant_id)

    resp = await client.post(
        f"/v1/services/{service_id}/topology/refresh", headers=bootstrap.headers
    )
    assert resp.status_code == 202, resp.text
    resp = await client.post(
        f"/v1/services/{service_id}/metrics/refresh", headers=bootstrap.headers
    )
    assert resp.status_code == 202

    kinds = [k for k, _ in queue.calls]
    assert kinds == ["topology_refresh", "metric_catalog_refresh"]
    assert all(
        p == {"tenant_id": str(bootstrap.tenant_id), "service_id": str(service_id)}
        for _, p in queue.calls
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_refresh_unknown_service_404(
    client_with_fake_queue: tuple[AsyncClient, _CapturingJobQueue],
    bootstrap: _Bootstrap,
) -> None:
    from ai_sre.utils.ids import new_id

    client, queue = client_with_fake_queue
    resp = await client.post(
        f"/v1/services/{new_id()}/metrics/refresh", headers=bootstrap.headers
    )
    assert resp.status_code == 404
    assert queue.calls == []


# ---- On-register enqueue ----


@pytest.mark.integration
@pytest.mark.asyncio
async def test_register_enqueues_both_refreshes(
    client_with_fake_queue: tuple[AsyncClient, _CapturingJobQueue],
    bootstrap: _Bootstrap,
) -> None:
    client, queue = client_with_fake_queue
    resp = await client.post(
        "/v1/services",
        headers=bootstrap.headers,
        json={"name": "checkout", "label_selector": {"app": "checkout"}},
    )
    assert resp.status_code == 201, resp.text
    service_id = resp.json()["id"]
    kinds = sorted(k for k, _ in queue.calls)
    assert kinds == ["metric_catalog_refresh", "topology_refresh"]
    assert all(p["service_id"] == service_id for _, p in queue.calls)


# ---- Periodic fan-out helper ----


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_services_for_refresh_enumerates_pairs(
    db_engine: AsyncEngine, bootstrap: _Bootstrap
) -> None:
    # bootstrap tenant has a service; a second tenant has none.
    service_id = await _create_service(bootstrap.tenant_id)
    await _bootstrap_tenant(slug="serviceless")

    pairs = await list_services_for_refresh()
    assert (str(bootstrap.tenant_id), str(service_id)) in pairs
    # Exactly one pair — the serviceless tenant contributes nothing.
    assert len(pairs) == 1


# ---- Tenant isolation on read routes ----


@respx.mock
@pytest.mark.integration
@pytest.mark.asyncio
async def test_topology_tenant_isolation(
    client: AsyncClient, bootstrap: _Bootstrap
) -> None:
    other = await _bootstrap_tenant(slug="other")
    service_id = await _create_service(bootstrap.tenant_id)

    # Other tenant can't read this service's topology.
    resp = await client.get(
        f"/v1/services/{service_id}/topology", headers=other.headers
    )
    assert resp.status_code == 404
