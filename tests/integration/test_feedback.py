"""Integration tests for feedback (spec 0013).

Covers the direct API (POST -> GET round trip, tenant isolation, 404) and the
Slack interactive callback (signed click persists a row; bad/stale signature
is rejected; a click on a deleted investigation is acknowledged, not a 500).
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from urllib.parse import urlencode

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_sre.api.deps import _get_envelope_crypto
from ai_sre.config import get_settings
from ai_sre.core.alert.repository import AlertRepository
from ai_sre.core.investigation.repository import InvestigationRepository
from ai_sre.core.service.repository import ServiceRepository
from ai_sre.core.tenant.api_key_service import ApiKeyService
from ai_sre.core.tenant.repository import ApiKeyRepository, TenantRepository
from ai_sre.core.tenant.service import TenantService
from ai_sre.db import session_scope
from ai_sre.utils.crypto import random_base64_key
from ai_sre.utils.ids import new_id
from ai_sre.utils.webhook_signature import slack_signature

_SIGNING_SECRET = "test-slack-signing-secret"


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SRE_TENANT_ENCRYPTION_KEY", random_base64_key())
    monkeypatch.setenv("AI_SRE_SLACK_SIGNING_SECRET", _SIGNING_SECRET)
    get_settings.cache_clear()
    _get_envelope_crypto.cache_clear()


async def _seed(*, slug: str) -> tuple[str, str]:
    """Create a tenant + service + alert + pending investigation.

    Returns (investigation_id, api_key).
    """
    now = datetime.now(UTC)
    async with session_scope() as session:
        tenant = await TenantService(TenantRepository(session)).create(name=slug, slug=slug)
        issued = await ApiKeyService(
            ApiKeyRepository(session), TenantRepository(session)
        ).issue(tenant.id, name="bootstrap")
        service = await ServiceRepository(session, tenant.id).create(
            name="checkout", label_selector={"app": "checkout"}, ownership=None, slo_config=None
        )
        alert = await AlertRepository(session, tenant.id).create(
            source="alertmanager", received_at=now, raw_payload={}, alert_name="X",
            severity="critical", status="firing", started_at=now, ended_at=None,
            labels={}, annotations={}, fingerprint="fp",
        )
        inv = await InvestigationRepository(session, tenant.id).create_pending(
            service_id=service.id, triggering_alert_id=alert.id, fingerprint="fp"
        )
        return str(inv.id), issued.key


def _signed_callback(inv_id: str, action_id: str = "feedback_useful") -> tuple[bytes, dict[str, str]]:
    payload = {
        "type": "block_actions",
        "user": {"id": "U1", "username": "alice"},
        "actions": [{"action_id": action_id, "value": inv_id, "type": "button"}],
    }
    body = urlencode({"payload": json.dumps(payload)}).encode("utf-8")
    ts = str(int(time.time()))
    sig = slack_signature(_SIGNING_SECRET, ts, body)
    headers = {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": sig,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    return body, headers


# ---- Direct API ----


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_then_get_feedback(client: AsyncClient, db_engine: AsyncEngine) -> None:
    inv_id, key = await _seed(slug="acme")
    headers = {"Authorization": f"Bearer {key}"}

    resp = await client.post(
        f"/v1/investigations/{inv_id}/feedback",
        headers=headers,
        json={"kind": "useful", "actor": "me", "body": "great"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["kind"] == "useful"

    resp = await client.get(f"/v1/investigations/{inv_id}/feedback", headers=headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["kind"] == "useful"
    assert rows[0]["body"] == "great"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_feedback_unknown_investigation_404(client: AsyncClient, db_engine: AsyncEngine) -> None:
    _, key = await _seed(slug="acme")
    headers = {"Authorization": f"Bearer {key}"}
    resp = await client.post(
        f"/v1/investigations/{new_id()}/feedback",
        headers=headers,
        json={"kind": "useful"},
    )
    assert resp.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_feedback_tenant_isolation(client: AsyncClient, db_engine: AsyncEngine) -> None:
    inv_id, _ = await _seed(slug="a")
    _, b_key = await _seed(slug="b")
    resp = await client.post(
        f"/v1/investigations/{inv_id}/feedback",
        headers={"Authorization": f"Bearer {b_key}"},
        json={"kind": "useful"},
    )
    assert resp.status_code == 404


# ---- Slack callback ----


@pytest.mark.integration
@pytest.mark.asyncio
async def test_slack_callback_persists_feedback(client: AsyncClient, db_engine: AsyncEngine) -> None:
    inv_id, key = await _seed(slug="acme")
    body, headers = _signed_callback(inv_id, "feedback_actual_cause")

    resp = await client.post("/v1/delivery/slack/callback", content=body, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}

    # Visible via the authenticated read endpoint.
    listed = await client.get(
        f"/v1/investigations/{inv_id}/feedback",
        headers={"Authorization": f"Bearer {key}"},
    )
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["kind"] == "actual_cause"
    assert rows[0]["actor"] == "alice"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_slack_callback_bad_signature_401(client: AsyncClient, db_engine: AsyncEngine) -> None:
    inv_id, _ = await _seed(slug="acme")
    body, headers = _signed_callback(inv_id)
    headers["X-Slack-Signature"] = "v0=deadbeef"
    resp = await client.post("/v1/delivery/slack/callback", content=body, headers=headers)
    assert resp.status_code == 401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_slack_callback_stale_timestamp_401(client: AsyncClient, db_engine: AsyncEngine) -> None:
    inv_id, _ = await _seed(slug="acme")
    payload = {
        "type": "block_actions",
        "user": {"id": "U1"},
        "actions": [{"action_id": "feedback_useful", "value": inv_id}],
    }
    body = urlencode({"payload": json.dumps(payload)}).encode("utf-8")
    ts = str(int(time.time()) - 600)
    headers = {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": slack_signature(_SIGNING_SECRET, ts, body),
    }
    resp = await client.post("/v1/delivery/slack/callback", content=body, headers=headers)
    assert resp.status_code == 401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_slack_callback_deleted_investigation_acknowledged(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    """A click on an unknown/deleted investigation must not 500."""
    # Don't seed; just sign a callback for a random id.
    body, headers = _signed_callback(str(new_id()))
    resp = await client.post("/v1/delivery/slack/callback", content=body, headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
