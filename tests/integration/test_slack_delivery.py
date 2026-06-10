"""End-to-end delivery test: orchestrator run posts to (mocked) Slack (spec 0010)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_sre.api.deps import _get_envelope_crypto
from ai_sre.config import get_settings
from ai_sre.core.alert.repository import AlertRepository
from ai_sre.core.integration.repository import IntegrationRepository
from ai_sre.core.integration.service import IntegrationService
from ai_sre.core.investigation.repository import InvestigationRepository
from ai_sre.core.service.repository import ServiceRepository
from ai_sre.core.tenant.repository import TenantRepository
from ai_sre.core.tenant.service import TenantService
from ai_sre.db import session_scope
from ai_sre.models.investigation import Investigation
from ai_sre.utils.crypto import random_base64_key
from ai_sre.workers.app import run_investigation


@pytest.fixture(autouse=True)
def _valid_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SRE_TENANT_ENCRYPTION_KEY", random_base64_key())
    get_settings.cache_clear()
    _get_envelope_crypto.cache_clear()


async def _seed_with_slack(*, slug: str) -> UUID:
    now = datetime.now(UTC)
    async with session_scope() as session:
        tenant = await TenantService(TenantRepository(session)).create(name=slug, slug=slug)
        await IntegrationService(
            IntegrationRepository(session, tenant.id), _get_envelope_crypto()
        ).create(
            kind="slack",
            name="slack",
            config={
                "bot_token": "xoxb-test",
                "team_id": "T1",
                "team_name": "Acme",
                "channel_id": "C123",
                "channel_name": "#oncall",
            },
        )
        service = await ServiceRepository(session, tenant.id).create(
            name="checkout", label_selector={"app": "checkout"}, ownership=None, slo_config=None
        )
        alert = await AlertRepository(session, tenant.id).create(
            source="alertmanager", received_at=now, raw_payload={}, alert_name="HighErrorRate",
            severity="critical", status="firing", started_at=now, ended_at=None,
            labels={"alertname": "HighErrorRate"}, annotations={}, fingerprint="fp",
        )
        inv = await InvestigationRepository(session, tenant.id).create_pending(
            service_id=service.id, triggering_alert_id=alert.id, fingerprint="fp"
        )
    return inv.id


@respx.mock
@pytest.mark.integration
@pytest.mark.asyncio
async def test_investigation_posts_report_to_slack(db_engine: AsyncEngine) -> None:
    route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "1700000000.000100"})
    )
    inv_id = await _seed_with_slack(slug="acme")

    await run_investigation(str(inv_id))

    # Investigation completed and a Slack message was posted to the configured channel.
    async with session_scope() as session:
        inv = await session.get(Investigation, inv_id)
        assert inv is not None and inv.status == "succeeded"

    assert route.called
    posted = json.loads(route.calls.last.request.content)
    assert posted["channel"] == "C123"
    assert posted["blocks"][0]["type"] == "header"


@respx.mock
@pytest.mark.integration
@pytest.mark.asyncio
async def test_investigation_without_slack_does_not_post(db_engine: AsyncEngine) -> None:
    """No Slack integration → no delivery, investigation still succeeds."""
    route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "1.1"})
    )
    now = datetime.now(UTC)
    async with session_scope() as session:
        tenant = await TenantService(TenantRepository(session)).create(name="noslack", slug="noslack")
        service = await ServiceRepository(session, tenant.id).create(
            name="checkout", label_selector={"app": "checkout"}, ownership=None, slo_config=None
        )
        alert = await AlertRepository(session, tenant.id).create(
            source="alertmanager", received_at=now, raw_payload={}, alert_name="X",
            severity="critical", status="firing", started_at=now, ended_at=None,
            labels={"alertname": "X"}, annotations={}, fingerprint="fp2",
        )
        inv = await InvestigationRepository(session, tenant.id).create_pending(
            service_id=service.id, triggering_alert_id=alert.id, fingerprint="fp2"
        )
        inv_id = inv.id

    await run_investigation(str(inv_id))

    async with session_scope() as session:
        inv2 = await session.get(Investigation, inv_id)
        assert inv2 is not None and inv2.status == "succeeded"
    assert not route.called
