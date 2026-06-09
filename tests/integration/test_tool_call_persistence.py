"""Integration test: ToolDispatcher -> ToolCallRepository persists a row.

Covers the core/llm seam end-to-end against a real DB: the dispatcher (in
llm/) persists through the concrete tenant-scoped ToolCallRepository (in
core/), with the full investigation -> stage -> tool_call FK chain.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_sre.core.alert.repository import AlertRepository
from ai_sre.core.investigation.budget import Budget
from ai_sre.core.investigation.context import InvestigationContext
from ai_sre.core.investigation.repository import (
    InvestigationRepository,
    ToolCallRepository,
)
from ai_sre.core.service.repository import ServiceRepository
from ai_sre.core.tenant.context import TenantContext
from ai_sre.core.tenant.repository import TenantRepository
from ai_sre.core.tenant.service import TenantService
from ai_sre.db import session_scope
from ai_sre.llm.tools import ToolDispatcher, ToolRegistry, ToolSpec
from ai_sre.models.investigation import ToolCall


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dispatch_persists_tool_call_row(db_engine: AsyncEngine) -> None:
    now = datetime.now(UTC)

    async def _ping(_input: dict[str, Any], _ctx: InvestigationContext) -> dict[str, Any]:
        return {"pong": True}

    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="ping", description="", input_schema={}, handler=_ping)
    )

    async with session_scope() as session:
        tenant = await TenantService(TenantRepository(session)).create(
            name="acme", slug="acme"
        )
        service = await ServiceRepository(session, tenant.id).create(
            name="checkout", label_selector={"app": "checkout"}, ownership=None, slo_config=None
        )
        alert = await AlertRepository(session, tenant.id).create(
            source="alertmanager", received_at=now, raw_payload={},
            alert_name="X", severity=None, status="firing", started_at=now,
            ended_at=None, labels={}, annotations={}, fingerprint="fp",
        )
        inv_repo = InvestigationRepository(session, tenant.id)
        inv = await inv_repo.create_pending(
            service_id=service.id, triggering_alert_id=alert.id, fingerprint="fp"
        )
        stage = await inv_repo.upsert_stage(
            inv.id, name="hypothesis", status="running", started_at=now
        )

        ctx = InvestigationContext(
            tenant=TenantContext(tenant_id=tenant.id, name="acme", slug="acme", api_key_id=tenant.id),
            investigation_id=inv.id,
            alert={}, service={}, dependencies={}, metric_catalog={},
            budget=Budget(),
        )
        ctx.current_stage_id = stage.id

        dispatcher = ToolDispatcher(registry, ToolCallRepository(session, tenant.id))
        out = await dispatcher.dispatch("ping", {"foo": "bar"}, ctx)
        await session.commit()

        assert out == {"pong": True}

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(ToolCall).where(ToolCall.investigation_id == inv.id)
            )
        ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.tool_name == "ping"
    assert row.outcome == "success"
    assert row.output == {"pong": True}
    assert row.stage_id == stage.id
