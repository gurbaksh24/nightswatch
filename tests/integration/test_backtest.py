"""Integration tests for backtest + replay (spec 0016)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_sre.api.deps import _get_envelope_crypto, get_embedder, get_job_queue
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
from ai_sre.utils.ids import new_id
from ai_sre.workers.app import run_investigation


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SRE_TENANT_ENCRYPTION_KEY", random_base64_key())
    monkeypatch.setenv("AI_SRE_EMBEDDING_PROVIDER", "hashing")
    get_settings.cache_clear()
    _get_envelope_crypto.cache_clear()
    get_embedder.cache_clear()


class _CapturingQueue(JobQueue):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def enqueue(self, kind: str, payload: dict[str, Any], *, delay_seconds: int = 0) -> JobId:
        self.calls.append((kind, payload))
        return f"job-{len(self.calls)}"

    async def cancel(self, job_id: JobId) -> None:
        return None


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


async def _seed(*, slug: str, with_slack: bool = False) -> tuple[str, UUID]:
    """Create tenant + api key + service (+ optional Slack). Returns (key, service_id)."""
    async with session_scope() as session:
        tenant = await TenantService(TenantRepository(session)).create(name=slug, slug=slug)
        issued = await ApiKeyService(
            ApiKeyRepository(session), TenantRepository(session)
        ).issue(tenant.id, name="bootstrap")
        service = await ServiceRepository(session, tenant.id).create(
            name="checkout", label_selector={"app": "checkout"}, ownership=None, slo_config=None
        )
        if with_slack:
            await IntegrationService(
                IntegrationRepository(session, tenant.id), _get_envelope_crypto()
            ).create(
                kind="slack", name="slack",
                config={"bot_token": "xoxb-test", "team_id": "T1", "team_name": "Acme",
                        "channel_id": "C123", "channel_name": "#oncall"},
            )
        return issued.key, service.id


def _payload(alertname: str = "HighErrorRate") -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "version": "4", "groupKey": "g", "status": "firing", "receiver": "ai-sre",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": alertname, "severity": "critical", "app": "checkout"},
                "annotations": {"summary": "errors high"},
                "startsAt": now,
            }
        ],
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_backtest_creates_investigation_and_enqueues(
    client_and_queue: tuple[AsyncClient, _CapturingQueue],
) -> None:
    client, queue = client_and_queue
    key, service_id = await _seed(slug="acme")

    resp = await client.post(
        "/v1/investigations/backtest",
        headers={"Authorization": f"Bearer {key}"},
        json={"alert_payload": _payload(), "service_id": str(service_id), "dry_run": True},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["is_backtest"] is True
    assert body["dry_run"] is True
    assert body["status"] == "pending"
    # Enqueued exactly once on the investigation queue.
    assert queue.calls == [("investigation", {"investigation_id": body["id"]})]


@respx.mock
@pytest.mark.integration
@pytest.mark.asyncio
async def test_backtest_dry_run_produces_report_without_slack(
    client_and_queue: tuple[AsyncClient, _CapturingQueue],
) -> None:
    slack = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "1.1"})
    )
    client, _queue = client_and_queue
    key, service_id = await _seed(slug="acme", with_slack=True)

    resp = await client.post(
        "/v1/investigations/backtest",
        headers={"Authorization": f"Bearer {key}"},
        json={"alert_payload": _payload(), "service_id": str(service_id), "dry_run": True},
    )
    inv_id = resp.json()["id"]

    # Run the enqueued investigation directly (the queue is faked in the API).
    await run_investigation(inv_id)

    # Complete record exists, report present, but nothing was posted to Slack.
    detail = await client.get(
        f"/v1/investigations/{inv_id}", headers={"Authorization": f"Bearer {key}"}
    )
    assert detail.status_code == 200
    assert detail.json()["status"] == "succeeded"
    assert detail.json()["report"] is not None
    assert not slack.called


@pytest.mark.integration
@pytest.mark.asyncio
async def test_backtest_unknown_service_404(
    client_and_queue: tuple[AsyncClient, _CapturingQueue],
) -> None:
    client, _ = client_and_queue
    key, _service_id = await _seed(slug="acme")
    resp = await client.post(
        "/v1/investigations/backtest",
        headers={"Authorization": f"Bearer {key}"},
        json={"alert_payload": _payload(), "service_id": str(new_id()), "dry_run": True},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "service.not_found"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_backtest_invalid_payload_422(
    client_and_queue: tuple[AsyncClient, _CapturingQueue],
) -> None:
    client, _ = client_and_queue
    key, service_id = await _seed(slug="acme")
    resp = await client.post(
        "/v1/investigations/backtest",
        headers={"Authorization": f"Bearer {key}"},
        json={"alert_payload": {"not": "an alertmanager payload"}, "service_id": str(service_id)},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "alert.invalid_payload"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_replay_duplicates_investigation(
    client_and_queue: tuple[AsyncClient, _CapturingQueue],
) -> None:
    client, queue = client_and_queue
    key, service_id = await _seed(slug="acme")

    # Seed an original via backtest (any prior investigation works).
    first = await client.post(
        "/v1/investigations/backtest",
        headers={"Authorization": f"Bearer {key}"},
        json={"alert_payload": _payload(), "service_id": str(service_id), "dry_run": True},
    )
    original_id = first.json()["id"]
    queue.calls.clear()

    resp = await client.post(
        f"/v1/investigations/{original_id}/replay",
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] != original_id
    assert body["replayed_from"] == original_id
    assert queue.calls == [("investigation", {"investigation_id": body["id"]})]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_replay_unknown_investigation_404(
    client_and_queue: tuple[AsyncClient, _CapturingQueue],
) -> None:
    client, _ = client_and_queue
    key, _ = await _seed(slug="acme")
    resp = await client.post(
        f"/v1/investigations/{new_id()}/replay",
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_replay_tenant_isolation(
    client_and_queue: tuple[AsyncClient, _CapturingQueue],
) -> None:
    client, _ = client_and_queue
    a_key, a_service = await _seed(slug="a")
    b_key, _ = await _seed(slug="b")
    first = await client.post(
        "/v1/investigations/backtest",
        headers={"Authorization": f"Bearer {a_key}"},
        json={"alert_payload": _payload(), "service_id": str(a_service), "dry_run": True},
    )
    a_inv = first.json()["id"]
    # Tenant B can't replay tenant A's investigation.
    resp = await client.post(
        f"/v1/investigations/{a_inv}/replay",
        headers={"Authorization": f"Bearer {b_key}"},
    )
    assert resp.status_code == 404
