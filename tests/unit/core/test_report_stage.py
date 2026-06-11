"""Unit tests for ReportStage (spec 0012): structured output + fallback."""

from __future__ import annotations

import pytest

from ai_sre.core.investigation.budget import Budget
from ai_sre.core.investigation.context import (
    Evidence,
    Hypothesis,
    InvestigationContext,
    TriageResult,
    ValidatedHypothesis,
)
from ai_sre.core.investigation.stages.report import ReportStage
from ai_sre.core.tenant.context import TenantContext
from ai_sre.llm.gateway import LLMGateway, LLMResponse
from ai_sre.utils.ids import new_id
from tests.fakes.llm import FakeLLMProvider


def _ctx(*, gateway: LLMGateway | None = None, validated=None, hypotheses=None) -> InvestigationContext:
    ctx = InvestigationContext(
        tenant=TenantContext(tenant_id=new_id(), name="t", slug="t", api_key_id=new_id()),
        investigation_id=new_id(),
        alert={"alert_name": "HighErrorRate", "severity": "critical", "labels": {}},
        service={"name": "checkout"},
        dependencies={}, metric_catalog={}, budget=Budget(),
    )
    ctx.triage = TriageResult(classification="novel")
    ctx.validated = validated or []
    ctx.hypotheses = hypotheses or []
    ctx.gateway = gateway
    return ctx


def _validated(statement: str, *, confirmed: bool | None, confidence: str = "high") -> ValidatedHypothesis:
    return ValidatedHypothesis(
        hypothesis_id="h1", statement=statement, confidence=confidence,
        confirmed=confirmed, evidence=[Evidence("query_prometheus", "rate high")], reasoning="r",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_structured_output_happy_path() -> None:
    provider = FakeLLMProvider()
    provider.queue_response(
        LLMResponse(
            text='{"headline": "DB pool exhausted", "confidence": "high", '
            '"hypotheses": [{"statement": "pool", "confidence": "high", "confirmed": true}], '
            '"next_actions": [{"action": "scale the pool"}]}'
        )
    )
    ctx = _ctx(gateway=LLMGateway(provider), validated=[_validated("pool", confirmed=True)])

    result = await ReportStage().execute(ctx)

    assert result.status == "succeeded"
    assert ctx.report is not None
    assert ctx.report.schema_version == "v1"
    assert ctx.report.headline == "DB pool exhausted"
    assert ctx.report.confidence == "high"
    assert ctx.report.next_actions == [{"action": "scale the pool"}]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_malformed_output_falls_back() -> None:
    provider = FakeLLMProvider()
    provider.queue_response(LLMResponse(text="not json"))
    provider.queue_response(LLMResponse(text="still not json"))  # corrective re-prompt
    ctx = _ctx(gateway=LLMGateway(provider), validated=[_validated("pool", confirmed=True)])

    result = await ReportStage().execute(ctx)

    assert result.status == "succeeded"
    assert result.output["fallback"] is True
    assert ctx.report.schema_version == "v1-fallback"
    assert ctx.report.headline.startswith("Likely cause: pool")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_gateway_deterministic_fallback() -> None:
    ctx = _ctx(gateway=None, validated=[_validated("pool", confirmed=True, confidence="medium")])
    result = await ReportStage().execute(ctx)
    assert result.output["fallback"] is True
    assert ctx.report.schema_version == "v1"
    assert ctx.report.headline == "Likely cause: pool"
    assert ctx.report.confidence == "medium"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fallback_no_confirmed_cause() -> None:
    ctx = _ctx(gateway=None, validated=[_validated("maybe", confirmed=None)])
    await ReportStage().execute(ctx)
    assert "without a confident root cause" in ctx.report.headline
    assert ctx.report.confidence == "low"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fallback_nothing_at_all() -> None:
    ctx = _ctx(gateway=None)
    await ReportStage().execute(ctx)
    assert "couldn't complete a diagnosis" in ctx.report.headline
    assert ctx.report.hypotheses == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fallback_uses_raw_hypotheses_when_unvalidated() -> None:
    ctx = _ctx(gateway=None, hypotheses=[Hypothesis(statement="cold cache")])
    await ReportStage().execute(ctx)
    assert ctx.report.hypotheses == [{"statement": "cold cache"}]
