"""Integration tests for the New Relic integration kind (spec 0019)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from ai_sre.api.deps import _get_envelope_crypto
from ai_sre.config import get_settings
from ai_sre.utils.crypto import random_base64_key

pytestmark = pytest.mark.integration

ADMIN_TOKEN = "test-admin-token"


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admin token + a real 32-byte encryption key (the default only decodes
    to 24 bytes and the crypto service is lru_cached per process)."""
    monkeypatch.setenv("AI_SRE_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("AI_SRE_TENANT_ENCRYPTION_KEY", random_base64_key())
    get_settings.cache_clear()
    _get_envelope_crypto.cache_clear()


async def _bootstrap_key(client: AsyncClient) -> str:
    admin = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    tenant = (
        await client.post("/v1/tenant", json={"name": "Acme", "slug": "acme"}, headers=admin)
    ).json()
    resp = await client.post(
        "/v1/auth/api-keys",
        json={"name": "bootstrap", "tenant_id": tenant["id"]},
        headers=admin,
    )
    return str(resp.json()["key"])


@pytest.mark.asyncio
async def test_create_newrelic_integration(client: AsyncClient) -> None:
    key = await _bootstrap_key(client)
    headers = {"Authorization": f"Bearer {key}"}

    resp = await client.post(
        "/v1/integrations",
        json={
            "kind": "newrelic",
            "name": "prod-newrelic",
            "config": {"account_id": 1234, "api_key": "NRAK-secret", "region": "EU"},
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Public view shows account + region and NEVER the API key.
    assert body["config_public"] == {"account_id": 1234, "region": "EU"}
    assert "NRAK-secret" not in resp.text
    # Spec 0020: creating a newrelic integration returns the one-time
    # webhook token (used as X-AI-SRE-Token by the NR workflow destination).
    assert isinstance(body.get("webhook_signing_secret"), str)
    assert body["webhook_signing_secret"]

    listed = (await client.get("/v1/integrations", headers=headers)).json()
    assert [i["kind"] for i in listed] == ["newrelic"]


@pytest.mark.asyncio
async def test_create_newrelic_rejects_malformed_config(client: AsyncClient) -> None:
    key = await _bootstrap_key(client)
    headers = {"Authorization": f"Bearer {key}"}
    resp = await client.post(
        "/v1/integrations",
        json={"kind": "newrelic", "name": "x", "config": {"api_key": "k"}},  # no account_id
        headers=headers,
    )
    assert resp.status_code == 422

    resp = await client.post(
        "/v1/integrations",
        json={"kind": "datadog", "name": "x", "config": {}},
        headers=headers,
    )
    assert resp.status_code == 422
