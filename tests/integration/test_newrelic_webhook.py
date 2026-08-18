"""End-to-end tests for the New Relic workflow webhook (spec 0020).

Mirrors ``test_alertmanager_webhook.py``: real DB, in-memory queue fake.
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_sre.api.deps import _get_envelope_crypto, get_job_queue
from ai_sre.config import get_settings
from ai_sre.core.integration.repository import IntegrationRepository
from ai_sre.core.integration.service import IntegrationService
from ai_sre.core.service.repository import ServiceRepository
from ai_sre.core.tenant.repository import TenantRepository
from ai_sre.core.tenant.service import TenantService
from ai_sre.db import session_scope
from ai_sre.models.alert import Alert
from ai_sre.queue.base import JobId, JobQueue
from ai_sre.utils.crypto import random_base64_key

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _valid_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SRE_TENANT_ENCRYPTION_KEY", random_base64_key())
    get_settings.cache_clear()
    _get_envelope_crypto.cache_clear()


@dataclass(frozen=True)
class _Tenant:
    tenant_id: UUID
    webhook_token: str


class _CapturingQueue(JobQueue):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def enqueue(self, kind: str, payload: dict[str, Any], *, delay_seconds: int = 0) -> JobId:
        self.calls.append((kind, payload))
        return f"job-{len(self.calls)}"

    async def cancel(self, job_id: JobId) -> None:
        return None


async def _bootstrap(*, slug: str = "acme") -> _Tenant:
    """Tenant + newrelic integration (with webhook token) + subject service."""
    async with session_scope() as session:
        tenant = await TenantService(TenantRepository(session)).create(name=slug, slug=slug)
        isvc = IntegrationService(IntegrationRepository(session, tenant.id), _get_envelope_crypto())
        integ = await isvc.create(
            kind="newrelic",
            name="prod",
            config={"account_id": 1234, "api_key": "NRAK-x", "region": "US"},
        )
        token = await isvc.generate_webhook_secret(integ.id)
        await ServiceRepository(session, tenant.id).create(
            name="checkout",
            label_selector={"app": "checkout"},
            ownership=None,
            slo_config=None,
        )
    return _Tenant(tenant_id=tenant.id, webhook_token=token)


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


def _payload(**overrides: object) -> dict[str, Any]:
    body: dict[str, Any] = {
        "issue_id": "issue-1",
        "title": "Error rate above 5%",
        "state": "ACTIVATED",
        "priority": "CRITICAL",
        "condition_name": "checkout error rate",
        "policy_name": "mfs-onchain",
        "entity_names": ["checkout"],
        "nrql": "SELECT count(*) FROM TransactionError",
        "route_group": "onchain",
        "runbook_url": "https://runbooks.example/checkout",
    }
    body.update(overrides)
    return body


async def test_open_issue_starts_investigation(
    client_and_queue: tuple[AsyncClient, _CapturingQueue],
) -> None:
    client, queue = client_and_queue
    tenant = await _bootstrap()

    resp = await client.post(
        f"/v1/webhooks/newrelic/{tenant.tenant_id}",
        json=_payload(),
        headers={"X-AI-SRE-Token": tenant.webhook_token},
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["accepted"] == 1
    assert len(queue.calls) == 1 and queue.calls[0][0] == "investigation"

    # The alert row keeps the ORIGINAL NR payload and the newrelic source.
    async with session_scope() as session:
        alert = (await session.execute(select(Alert))).scalars().one()
        assert alert.source == "newrelic"
        assert alert.alert_name == "checkout error rate"
        assert alert.labels["severity"] == "critical"
        assert alert.annotations["runbook_url"] == "https://runbooks.example/checkout"
        assert alert.raw_payload["issue_id"] == "issue-1"
        assert alert.investigation_id is not None


async def test_repeat_issue_dedupes(
    client_and_queue: tuple[AsyncClient, _CapturingQueue],
) -> None:
    client, queue = client_and_queue
    tenant = await _bootstrap()
    headers = {"X-AI-SRE-Token": tenant.webhook_token}

    r1 = await client.post(
        f"/v1/webhooks/newrelic/{tenant.tenant_id}", json=_payload(), headers=headers
    )
    # Same condition+entity, new issue occurrence → links, no second job.
    r2 = await client.post(
        f"/v1/webhooks/newrelic/{tenant.tenant_id}",
        json=_payload(issue_id="issue-2"),
        headers=headers,
    )
    assert r1.status_code == r2.status_code == 202
    assert len(queue.calls) == 1


async def test_closed_state_acknowledged_not_ingested(
    client_and_queue: tuple[AsyncClient, _CapturingQueue],
) -> None:
    client, queue = client_and_queue
    tenant = await _bootstrap()
    resp = await client.post(
        f"/v1/webhooks/newrelic/{tenant.tenant_id}",
        json=_payload(state="CLOSED"),
        headers={"X-AI-SRE-Token": tenant.webhook_token},
    )
    assert resp.status_code == 202
    assert resp.json() == {"accepted": 0, "alert_ids": []}
    assert queue.calls == []


async def test_bad_or_missing_token_is_401(
    client_and_queue: tuple[AsyncClient, _CapturingQueue],
) -> None:
    client, _queue = client_and_queue
    tenant = await _bootstrap()
    url = f"/v1/webhooks/newrelic/{tenant.tenant_id}"

    assert (await client.post(url, json=_payload())).status_code == 401
    assert (
        await client.post(url, json=_payload(), headers={"X-AI-SRE-Token": "wrong"})
    ).status_code == 401
    # Alertmanager's HMAC header doesn't work here either.
    assert (
        await client.post(url, json=_payload(), headers={"X-AI-SRE-Signature": "sha256=deadbeef"})
    ).status_code == 401


async def test_malformed_payload_is_400(
    client_and_queue: tuple[AsyncClient, _CapturingQueue],
) -> None:
    client, _queue = client_and_queue
    tenant = await _bootstrap()
    resp = await client.post(
        f"/v1/webhooks/newrelic/{tenant.tenant_id}",
        content=json.dumps({"title": "no condition or issue id"}),
        headers={
            "X-AI-SRE-Token": tenant.webhook_token,
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "validation.failed"
