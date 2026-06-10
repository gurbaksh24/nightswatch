"""End-to-end HypothesisStage test (spec 0009).

A scripted FakeLLMProvider emits two tool_use turns (get_alert_details +
list_metric_names) then a structured hypothesis list. We assert the
hypotheses land in context and that tool_call rows are persisted.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_sre.core.alert.repository import AlertRepository
from ai_sre.core.investigation.budget import Budget
from ai_sre.core.investigation.context import InvestigationContext, TriageResult
from ai_sre.core.investigation.repository import (
    InvestigationRepository,
    ToolCallRepository,
)
from ai_sre.core.investigation.stages.hypothesis import HypothesisStage
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
async def test_hypothesis_stage_runs_tools_and_parses(db_engine: AsyncEngine) -> None:
    register_builtin_tools(REGISTRY)
    now = datetime.now(UTC)

    fake = FakeLLMProvider()
    fake.queue_response(
        LLMResponse(text="", tool_calls=[{"id": "t1", "name": "get_alert_details", "input": {}}])
    )
    fake.queue_response(
        LLMResponse(
            text="",
            tool_calls=[{"id": "t2", "name": "list_metric_names", "input": {"filter": "http"}}],
        )
    )
    fake.queue_response(
        LLMResponse(
            text=(
                '{"hypotheses": [{"statement": "DB connection pool exhausted", '
                '"initial_confidence": 0.7, "evidence_refs": ["t2"]}]}'
            )
        )
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
        stage = await inv_repo.upsert_stage(inv.id, name="hypothesis", status="running", started_at=now)

        ctx = InvestigationContext(
            tenant=TenantContext(tenant_id=tenant.id, name="acme", slug="acme", api_key_id=tenant.id),
            investigation_id=inv.id,
            alert={"alert_name": "HighErrorRate", "labels": {"alertname": "HighErrorRate"}, "severity": "critical"},
            service={"name": "checkout", "label_selector": {"app": "checkout"}},
            dependencies={"upstream": [], "downstream": []},
            metric_catalog={"metrics": [{"metric_name": "http_requests_total"}]},
            budget=Budget(),
        )
        ctx.triage = TriageResult(classification="novel")
        ctx.current_stage_id = stage.id
        ctx.gateway = LLMGateway(fake)
        ctx.dispatcher = ToolDispatcher(REGISTRY, ToolCallRepository(session, tenant.id))

        result = await HypothesisStage().execute(ctx)
        await session.commit()

    assert result.status == "succeeded"
    assert len(ctx.hypotheses) == 1
    assert ctx.hypotheses[0].statement == "DB connection pool exhausted"
    assert ctx.hypotheses[0].initial_confidence == pytest.approx(0.7)

    async with session_scope() as session:
        rows = (
            await session.execute(select(ToolCall).where(ToolCall.investigation_id == inv.id))
        ).scalars().all()
    names = sorted(r.tool_name for r in rows)
    assert names == ["get_alert_details", "list_metric_names"]
    assert all(r.outcome == "success" for r in rows)
