"""Integration tests for the Slack OAuth flow (spec 0010)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_sre.api.deps import _get_envelope_crypto
from ai_sre.api.integrations import _sign_oauth_state
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
    monkeypatch.setenv("AI_SRE_TENANT_ENCRYPTION_KEY", random_base64_key())
    monkeypatch.setenv("AI_SRE_SLACK_CLIENT_ID", "client-123")
    get_settings.cache_clear()
    _get_envelope_crypto.cache_clear()


@dataclass(frozen=True)
class _Bootstrap:
    tenant_id: UUID
    headers: dict[str, str]


async def _bootstrap(*, slug: str) -> _Bootstrap:
    async with session_scope() as session:
        tenant = await TenantService(TenantRepository(session)).create(name=slug, slug=slug)
        issued = await ApiKeyService(
            ApiKeyRepository(session), TenantRepository(session)
        ).issue(tenant.id, name="bootstrap")
    return _Bootstrap(tenant.id, {"Authorization": f"Bearer {issued.key}"})


@pytest.mark.integration
@pytest.mark.asyncio
async def test_oauth_start_redirects_to_slack(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    b = await _bootstrap(slug="acme")
    resp = await client.get("/v1/integrations/slack/oauth/start", headers=b.headers)
    assert resp.status_code == 307
    location = resp.headers["location"]
    assert location.startswith("https://slack.com/oauth/v2/authorize")
    assert "client_id=client-123" in location
    assert "state=" in location


@respx.mock
@pytest.mark.integration
@pytest.mark.asyncio
async def test_oauth_callback_persists_slack_integration(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    b = await _bootstrap(slug="acme")
    respx.post("https://slack.com/api/oauth.v2.access").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "access_token": "xoxb-secret-token",
                "team": {"id": "T123", "name": "Acme Inc"},
                "incoming_webhook": {"channel": "#oncall", "channel_id": "C999"},
            },
        )
    )
    state = _sign_oauth_state(b.tenant_id)

    resp = await client.get(
        f"/v1/integrations/slack/oauth/callback?code=the-code&state={state}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "connected"
    assert body["team_id"] == "T123"
    assert body["channel_id"] == "C999"

    # Integration persisted: public config has team/channel, secret is encrypted.
    async with session_scope() as session:
        service = IntegrationService(
            IntegrationRepository(session, b.tenant_id), _get_envelope_crypto()
        )
        integrations = await service.list()
        slack = next(i for i in integrations if i.kind == "slack")
        assert slack.config_public["team_id"] == "T123"
        assert slack.config_public["channel_id"] == "C999"
        assert "bot_token" not in slack.config_public  # secret stays encrypted
        assert service.decrypt_config(slack)["bot_token"] == "xoxb-secret-token"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_oauth_callback_bad_state_400(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    resp = await client.get(
        "/v1/integrations/slack/oauth/callback?code=x&state=garbage-state"
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "validation.failed"
