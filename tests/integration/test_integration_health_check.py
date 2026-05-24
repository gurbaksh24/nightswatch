"""End-to-end test for the integration health-check flow.

Covers spec 0003's integration acceptance criteria:

    * ``POST /v1/integrations/{id}/health-check`` enqueues a job (no real
      worker process needed — we capture the enqueue and execute the
      worker body inline via the extracted ``run_health_check`` helper).
    * Running the worker body against a respx-mocked Prometheus updates
      the integration row: ``status`` flips from ``pending`` →
      ``healthy``/``unhealthy``; ``last_health_check_at`` is set.

Procrastinate itself is bypassed by overriding the FastAPI ``get_job_queue``
dep with an in-memory fake. The actual ProcrastinateJobQueue is unit-tested
elsewhere; this test focuses on the route + worker-body wiring.
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
from ai_sre.core.tenant.api_key_service import ApiKeyService
from ai_sre.core.tenant.repository import ApiKeyRepository, TenantRepository
from ai_sre.core.tenant.service import TenantService
from ai_sre.db import session_scope
from ai_sre.queue.base import JobId, JobQueue
from ai_sre.utils.crypto import random_base64_key
from ai_sre.workers.app import run_health_check


@dataclass(frozen=True)
class _Bootstrap:
    tenant_id: UUID
    api_key: str
    headers: dict[str, str]


@pytest.fixture(autouse=True)
def _valid_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a real 32-byte encryption key (the .env.example default
    intentionally doesn't decode to 32 bytes) and clear cached singletons
    that depend on settings.
    """
    monkeypatch.setenv("AI_SRE_TENANT_ENCRYPTION_KEY", random_base64_key())
    get_settings.cache_clear()
    _get_envelope_crypto.cache_clear()


async def _bootstrap_tenant(*, slug: str) -> _Bootstrap:
    """Insert a tenant + issue an API key via the service layer."""
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
    tenant_id: UUID, *, url: str = "http://prom-test.example.com"
) -> UUID:
    """Insert a Prometheus integration directly so we don't need the API
    pre-tested in this file."""
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


class _CapturingJobQueue(JobQueue):
    """In-memory queue that records every enqueue. Cancels are no-ops."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        delay_seconds: int = 0,
    ) -> JobId:
        self.calls.append((kind, payload))
        return f"fake-job-{len(self.calls)}"

    async def cancel(self, job_id: JobId) -> None:
        return None


@pytest_asyncio.fixture
async def client_with_fake_queue(
    db_engine: AsyncEngine,
) -> AsyncIterator[tuple[AsyncClient, _CapturingJobQueue]]:
    """Yield an httpx AsyncClient whose ``get_job_queue`` dep is overridden
    to use a capturing in-memory queue. Returns the client and the queue so
    the test can inspect what got enqueued.
    """
    from ai_sre.main import create_app

    app = create_app()
    queue = _CapturingJobQueue()
    app.dependency_overrides[get_job_queue] = lambda: queue

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, queue


@pytest_asyncio.fixture
async def bootstrap(db_engine: AsyncEngine) -> _Bootstrap:
    return await _bootstrap_tenant(slug="acme")


# ---- Route tests ----


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_check_enqueues_job(
    client_with_fake_queue: tuple[AsyncClient, _CapturingJobQueue],
    bootstrap: _Bootstrap,
) -> None:
    client, queue = client_with_fake_queue
    integration_id = await _create_prom_integration(bootstrap.tenant_id)

    resp = await client.post(
        f"/v1/integrations/{integration_id}/health-check",
        headers=bootstrap.headers,
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["integration_id"] == str(integration_id)
    assert body["status"] == "queued"
    assert body["job_id"].startswith("fake-job-")

    # And the queue saw exactly one health_check enqueue with the right payload.
    assert len(queue.calls) == 1
    kind, payload = queue.calls[0]
    assert kind == "health_check"
    assert payload == {
        "tenant_id": str(bootstrap.tenant_id),
        "integration_id": str(integration_id),
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_check_404_for_other_tenants_integration(
    client_with_fake_queue: tuple[AsyncClient, _CapturingJobQueue],
    bootstrap: _Bootstrap,
) -> None:
    """Tenant A tries to health-check Tenant B's integration → 404, not 202."""
    other = await _bootstrap_tenant(slug="other")
    other_integration_id = await _create_prom_integration(other.tenant_id)

    client, queue = client_with_fake_queue
    resp = await client.post(
        f"/v1/integrations/{other_integration_id}/health-check",
        headers=bootstrap.headers,
    )
    assert resp.status_code == 404
    # No enqueue happened.
    assert queue.calls == []


# ---- Worker-body tests ----
#
# These drive the extracted ``run_health_check`` directly with a respx
# mock standing in for Prometheus, then assert the integration row reflects
# the outcome.


@respx.mock
@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_marks_healthy_on_200(
    db_engine: AsyncEngine, bootstrap: _Bootstrap
) -> None:
    url = "http://prom-healthy.example.com"
    respx.get(f"{url}/-/healthy").mock(return_value=httpx.Response(200))
    integration_id = await _create_prom_integration(bootstrap.tenant_id, url=url)

    await run_health_check(str(bootstrap.tenant_id), str(integration_id))

    async with session_scope() as session:
        repo = IntegrationRepository(session, bootstrap.tenant_id)
        row = await repo.get(integration_id)
        assert row is not None
        assert row.status == "healthy"
        assert row.last_health_check_at is not None


@respx.mock
@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_marks_unhealthy_on_503(
    db_engine: AsyncEngine, bootstrap: _Bootstrap
) -> None:
    url = "http://prom-sick.example.com"
    respx.get(f"{url}/-/healthy").mock(return_value=httpx.Response(503))
    integration_id = await _create_prom_integration(bootstrap.tenant_id, url=url)

    await run_health_check(str(bootstrap.tenant_id), str(integration_id))

    async with session_scope() as session:
        repo = IntegrationRepository(session, bootstrap.tenant_id)
        row = await repo.get(integration_id)
        assert row is not None
        assert row.status == "unhealthy"
        assert row.last_health_check_at is not None


@respx.mock
@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_marks_unhealthy_on_transport_error(
    db_engine: AsyncEngine, bootstrap: _Bootstrap
) -> None:
    url = "http://prom-unreachable.example.com"
    respx.get(f"{url}/-/healthy").mock(
        side_effect=httpx.ConnectError("name resolution failed")
    )
    integration_id = await _create_prom_integration(bootstrap.tenant_id, url=url)

    await run_health_check(str(bootstrap.tenant_id), str(integration_id))

    async with session_scope() as session:
        repo = IntegrationRepository(session, bootstrap.tenant_id)
        row = await repo.get(integration_id)
        assert row is not None
        assert row.status == "unhealthy"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_is_idempotent_against_missing_integration(
    db_engine: AsyncEngine, bootstrap: _Bootstrap
) -> None:
    """If the integration was deleted between enqueue and worker pickup, the
    task should log + return cleanly, not raise.
    """
    from ai_sre.utils.ids import new_id

    # No integration with this id exists for this tenant.
    fake_id = new_id()
    await run_health_check(str(bootstrap.tenant_id), str(fake_id))
    # Reaching here without exception is the assertion.
