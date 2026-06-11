"""End-to-end test for the investigation read endpoints (spec 0012).

Runs a full (keyless → fallback report) investigation, then reads it back via
the HTTP API: list, detail, report, trace.
"""

from __future__ import annotations

from datetime import UTC, datetime

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
from ai_sre.workers.app import run_investigation


@pytest.fixture(autouse=True)
def _valid_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SRE_TENANT_ENCRYPTION_KEY", random_base64_key())
    get_settings.cache_clear()
    _get_envelope_crypto.cache_clear()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_read_endpoints_end_to_end(client: AsyncClient, db_engine: AsyncEngine) -> None:
    now = datetime.now(UTC)
    async with session_scope() as session:
        tenant = await TenantService(TenantRepository(session)).create(name="acme", slug="acme")
        issued = await ApiKeyService(
            ApiKeyRepository(session), TenantRepository(session)
        ).issue(tenant.id, name="bootstrap")
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
        inv_id = inv.id

    headers = {"Authorization": f"Bearer {issued.key}"}

    # Run the full pipeline (no LLM key → fallback report persisted).
    await run_investigation(str(inv_id))

    # --- list ---
    resp = await client.get("/v1/investigations", headers=headers)
    assert resp.status_code == 200, resp.text
    listed = resp.json()
    assert any(i["id"] == str(inv_id) and i["status"] == "succeeded" for i in listed)

    # --- list with a status filter that excludes it ---
    resp = await client.get("/v1/investigations?status=failed", headers=headers)
    assert resp.json() == []

    # --- detail (+ report) ---
    resp = await client.get(f"/v1/investigations/{inv_id}", headers=headers)
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["status"] == "succeeded"
    assert detail["report"] is not None
    assert detail["report"]["headline"]

    # --- report ---
    resp = await client.get(f"/v1/investigations/{inv_id}/report", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["schema_version"] == "v1"

    # --- trace: all five stages present ---
    resp = await client.get(f"/v1/investigations/{inv_id}/trace", headers=headers)
    assert resp.status_code == 200
    trace = resp.json()
    assert {s["name"] for s in trace["stages"]} == {
        "triage", "context_assembly", "hypothesis", "validation", "report"
    }

    # --- 404 for an unknown id ---
    resp = await client.get(f"/v1/investigations/{new_id()}", headers=headers)
    assert resp.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tenant_isolation_on_read(client: AsyncClient, db_engine: AsyncEngine) -> None:
    """Tenant B can't read tenant A's investigation."""
    now = datetime.now(UTC)
    async with session_scope() as session:
        a = await TenantService(TenantRepository(session)).create(name="a", slug="a")
        service = await ServiceRepository(session, a.id).create(
            name="s", label_selector={"app": "a"}, ownership=None, slo_config=None
        )
        alert = await AlertRepository(session, a.id).create(
            source="alertmanager", received_at=now, raw_payload={}, alert_name="X",
            severity="critical", status="firing", started_at=now, ended_at=None,
            labels={"alertname": "X"}, annotations={}, fingerprint="fp",
        )
        inv = await InvestigationRepository(session, a.id).create_pending(
            service_id=service.id, triggering_alert_id=alert.id, fingerprint="fp"
        )
        a_inv_id = inv.id
        b = await TenantService(TenantRepository(session)).create(name="b", slug="b")
        b_key = await ApiKeyService(
            ApiKeyRepository(session), TenantRepository(session)
        ).issue(b.id, name="b")

    resp = await client.get(
        f"/v1/investigations/{a_inv_id}", headers={"Authorization": f"Bearer {b_key.key}"}
    )
    assert resp.status_code == 404
