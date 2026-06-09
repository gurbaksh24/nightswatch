"""End-to-end tests for the Alertmanager webhook (spec 0006).

Drives POST /v1/webhooks/alertmanager/{tenant_id} against a real DB. The job
queue is overridden with an in-memory fake (Procrastinate itself is unit-
tested elsewhere); we assert DB rows + what got enqueued.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
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
from ai_sre.models.alert import Alert
from ai_sre.models.investigation import Investigation
from ai_sre.queue.base import JobId, JobQueue
from ai_sre.utils.crypto import random_base64_key
from ai_sre.utils.webhook_signature import sign


@pytest.fixture(autouse=True)
def _valid_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SRE_TENANT_ENCRYPTION_KEY", random_base64_key())
    get_settings.cache_clear()
    _get_envelope_crypto.cache_clear()


@dataclass(frozen=True)
class _Tenant:
    tenant_id: UUID
    webhook_secret: str


class _CapturingQueue(JobQueue):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def enqueue(self, kind: str, payload: dict[str, Any], *, delay_seconds: int = 0) -> JobId:
        self.calls.append((kind, payload))
        return f"job-{len(self.calls)}"

    async def cancel(self, job_id: JobId) -> None:
        return None


async def _bootstrap(*, slug: str, with_service: bool = True) -> _Tenant:
    """Create tenant + a prometheus integration (with a webhook secret) +
    optionally the subject service."""
    async with session_scope() as session:
        tenant = await TenantService(TenantRepository(session)).create(
            name=slug, slug=slug
        )
        # api key issued for completeness; the webhook doesn't use it.
        await ApiKeyService(
            ApiKeyRepository(session), TenantRepository(session)
        ).issue(tenant.id, name="bootstrap")
        isvc = IntegrationService(
            IntegrationRepository(session, tenant.id), _get_envelope_crypto()
        )
        integ = await isvc.create(
            kind="prometheus", name="prod", config={"url": "http://p", "auth": {"type": "none"}}
        )
        secret = await isvc.generate_webhook_secret(integ.id)
        if with_service:
            await ServiceRepository(session, tenant.id).create(
                name="checkout",
                label_selector={"app": "checkout"},
                ownership=None,
                slo_config=None,
            )
    return _Tenant(tenant_id=tenant.id, webhook_secret=secret)


@pytest_asyncio.fixture
async def client_and_queue(
    db_engine: AsyncEngine,
) -> AsyncIterator[tuple[AsyncClient, _CapturingQueue]]:
    from ai_sre.main import create_app

    app = create_app()
    queue = _CapturingQueue()
    app.dependency_overrides[get_job_queue] = lambda: queue
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, queue


def _payload(alertname: str = "HighErrorRate", **extra_labels: str) -> bytes:
    body = {
        "version": "4",
        "groupKey": "g",
        "status": "firing",
        "receiver": "r",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": alertname, "severity": "critical", **extra_labels},
                "annotations": {"summary": "boom"},
                "startsAt": "2026-01-01T00:00:00Z",
            }
        ],
    }
    return json.dumps(body).encode("utf-8")


async def _count(model: type[Alert] | type[Investigation], tenant_id: UUID) -> int:
    async with session_scope() as session:
        result = await session.execute(
            select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
        )
        return int(result.scalar_one())


async def _post(
    client: AsyncClient, tenant_id: UUID, raw: bytes, *, secret: str | None
) -> Any:
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers["X-AI-SRE-Signature"] = sign(secret, raw)
    return await client.post(
        f"/v1/webhooks/alertmanager/{tenant_id}", content=raw, headers=headers
    )


# ---- happy path ----


@pytest.mark.integration
@pytest.mark.asyncio
async def test_signed_webhook_creates_investigation(
    client_and_queue: tuple[AsyncClient, _CapturingQueue],
) -> None:
    client, queue = client_and_queue
    t = await _bootstrap(slug="acme")
    raw = _payload()

    resp = await _post(client, t.tenant_id, raw, secret=t.webhook_secret)

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["accepted"] == 1
    assert len(body["alert_ids"]) == 1
    assert await _count(Alert, t.tenant_id) == 1
    assert await _count(Investigation, t.tenant_id) == 1
    # The investigation was enqueued.
    assert len(queue.calls) == 1
    assert queue.calls[0][0] == "investigation"


# ---- signature failures ----


@pytest.mark.integration
@pytest.mark.asyncio
async def test_wrong_signature_401(
    client_and_queue: tuple[AsyncClient, _CapturingQueue],
) -> None:
    client, queue = client_and_queue
    t = await _bootstrap(slug="acme")
    raw = _payload()

    resp = await _post(client, t.tenant_id, raw, secret="not-the-secret")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "auth.invalid_signature"
    assert await _count(Alert, t.tenant_id) == 0
    assert queue.calls == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_missing_signature_401(
    client_and_queue: tuple[AsyncClient, _CapturingQueue],
) -> None:
    client, _ = client_and_queue
    t = await _bootstrap(slug="acme")
    resp = await _post(client, t.tenant_id, _payload(), secret=None)
    assert resp.status_code == 401
    assert await _count(Alert, t.tenant_id) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unknown_tenant_401_not_500(
    client_and_queue: tuple[AsyncClient, _CapturingQueue],
) -> None:
    """A tenant with no integration can't be verified → 401, no leak."""
    from ai_sre.utils.ids import new_id

    client, _ = client_and_queue
    raw = _payload()
    resp = await _post(client, new_id(), raw, secret="whatever")
    assert resp.status_code == 401


# ---- dedupe ----


@pytest.mark.integration
@pytest.mark.asyncio
async def test_duplicate_within_window_no_new_investigation(
    client_and_queue: tuple[AsyncClient, _CapturingQueue],
) -> None:
    client, queue = client_and_queue
    t = await _bootstrap(slug="acme")

    r1 = await _post(client, t.tenant_id, _payload(), secret=t.webhook_secret)
    # Same alert, only an unstable label differs → same fingerprint.
    r2 = await _post(client, t.tenant_id, _payload(pod="pod-2"), secret=t.webhook_secret)

    assert r1.status_code == 202 and r2.status_code == 202
    assert await _count(Alert, t.tenant_id) == 2  # both alerts persisted
    assert await _count(Investigation, t.tenant_id) == 1  # deduped to one
    assert len(queue.calls) == 1  # only the first enqueued


# ---- validation ----


@pytest.mark.integration
@pytest.mark.asyncio
async def test_missing_alertname_400(
    client_and_queue: tuple[AsyncClient, _CapturingQueue],
) -> None:
    client, _ = client_and_queue
    t = await _bootstrap(slug="acme")
    body = {
        "version": "4",
        "groupKey": "g",
        "status": "firing",
        "receiver": "r",
        "alerts": [
            {
                "status": "firing",
                "labels": {"severity": "warning"},  # no alertname
                "annotations": {},
                "startsAt": "2026-01-01T00:00:00Z",
            }
        ],
    }
    raw = json.dumps(body).encode("utf-8")
    resp = await _post(client, t.tenant_id, raw, secret=t.webhook_secret)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "validation.failed"
    assert await _count(Alert, t.tenant_id) == 0
