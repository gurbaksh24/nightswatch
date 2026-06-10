"""End-to-end ValidationStage test against a real DB (spec 0011).

A scripted FakeLLMProvider validates two hypotheses (one tool_use turn + a
verdict each). Asserts ctx.validated is populated and tool_call rows persist.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_sre.core.alert.repository import AlertRepository
from ai_sre.core.investigation.budget import Budget
from ai_sre.core.investigation.context import Hypothesis, InvestigationContext
from ai_sre.core.investigation.repository import (
    InvestigationRepository,
    ToolCallRepository,
)
from ai_sre.core.investigation.stages.validation import ValidationStage
from ai_sre.core.service.repository import ServiceRepository
from ai_sre.core.tenant.context import TenantContext
from ai_sre.core.tenant.repository import TenantRepository
from ai_sre.core.tenant.service import TenantService
from ai_sre.db import session_scope
from ai_sre.llm.gateway import LLMGateway, LLMResponse
from ai_sre.llm.tools import REGISTRY, ToolDispatcher, register_builtin_tools
from ai_sre.models.investigation import ToolCall
from tests.fakes.llm import FakeLLMProvider


@pytest.mark.integration
@pytest.mark.asyncio
async def test_validation_populates_context_and_persists_tool_calls(
    db_engine: AsyncEngine,
) -> None:
    register_builtin_tools(REGISTRY)
    now = datetime.now(UTC)

    fake = FakeLLMProvider()
    for verdict in ("confirmed", "refuted"):
        fake.queue_response(
            LLMResponse(text="", tool_calls=[{"id": "t", "name": "get_alert_details", "input": {}}])
        )
        fake.queue_response(
            LLMResponse(text=f'{{"verdict": "{verdict}", "confidence": 0.7, "reasoning": "x"}}')
        )

    async with session_scope() as session:
        tenant = await TenantService(TenantRepository(session)).create(name="acme", slug="acme")
        service = await ServiceRepository(session, tenant.id).create(
            name="checkout", label_selector={"app": "checkout"}, ownership=None, slo_config=None
        )
        alert = await AlertRepository(session, tenant.id).create(
            source="alertmanager", received_at=now, raw_payload={}, alert_name="HighErrorRate",
            severity="critical", status="firing", started_at=now, ended_at=None,
            labels={"alertname": "HighErrorRate"}, annotations={}, fingerprint="fp",
        )
        inv_repo = InvestigationRepository(session, tenant.id)
        inv = await inv_repo.create_pending(
            service_id=service.id, triggering_alert_id=alert.id, fingerprint="fp"
        )
        stage = await inv_repo.upsert_stage(inv.id, name="validation", status="running", started_at=now)

        ctx = InvestigationContext(
            tenant=TenantContext(tenant_id=tenant.id, name="acme", slug="acme", api_key_id=tenant.id),
            investigation_id=inv.id,
            alert={"alert_name": "HighErrorRate"},
            service={"name": "checkout"},
            dependencies={}, metric_catalog={}, budget=Budget(),
        )
        ctx.hypotheses = [
            Hypothesis(statement="DB pool exhausted", initial_confidence=0.6),
            Hypothesis(statement="Upstream latency", initial_confidence=0.4),
        ]
        ctx.current_stage_id = stage.id
        ctx.gateway = LLMGateway(fake)
        ctx.dispatcher = ToolDispatcher(REGISTRY, ToolCallRepository(session, tenant.id))

        result = await ValidationStage().execute(ctx)
        await session.commit()
        inv_id = inv.id

    assert result.status == "succeeded"
    assert len(ctx.validated) == 2
    assert ctx.validated[0].confirmed is True
    assert ctx.validated[1].confirmed is False
    assert all(v.evidence for v in ctx.validated)

    async with session_scope() as session:
        rows = (
            await session.execute(select(ToolCall).where(ToolCall.investigation_id == inv_id))
        ).scalars().all()
    # One get_alert_details per hypothesis.
    assert len(rows) == 2
    assert all(r.tool_name == "get_alert_details" and r.outcome == "success" for r in rows)
