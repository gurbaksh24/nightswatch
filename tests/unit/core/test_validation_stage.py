"""Unit tests for ValidationStage (spec 0011).

Real LLMGateway + FakeLLMProvider drive the tool-loop; a fake dispatcher
stands in for tool execution (no DB). Each hypothesis is scripted with one
tool_use turn then a verdict JSON.
"""

from __future__ import annotations

from typing import Any

import pytest

from ai_sre.core.investigation.budget import Budget
from ai_sre.core.investigation.context import (
    Hypothesis,
    InvestigationContext,
)
from ai_sre.core.investigation.stages.validation import ValidationStage
from ai_sre.core.tenant.context import TenantContext
from ai_sre.llm.gateway import LLMGateway, LLMResponse
from ai_sre.utils.ids import new_id
from tests.fakes.llm import FakeLLMProvider


class _FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def dispatch(self, name: str, input: dict[str, Any], ctx: Any) -> dict[str, Any]:
        self.calls.append(name)
        return {"ok": True, "tool": name}


def _tool_use(idx: int) -> LLMResponse:
    return LLMResponse(text="", tool_calls=[{"id": f"t{idx}", "name": "get_alert_details", "input": {}}])


def _verdict(verdict: str, confidence: float) -> LLMResponse:
    return LLMResponse(text=f'{{"verdict": "{verdict}", "confidence": {confidence}, "reasoning": "data {verdict}"}}')


def _ctx(hyps: list[Hypothesis], provider: FakeLLMProvider, *, budget: Budget | None = None) -> InvestigationContext:
    ctx = InvestigationContext(
        tenant=TenantContext(tenant_id=new_id(), name="t", slug="t", api_key_id=new_id()),
        investigation_id=new_id(),
        alert={"alert_name": "X"},
        service={},
        dependencies={},
        metric_catalog={},
        budget=budget or Budget(),
    )
    ctx.hypotheses = hyps
    ctx.gateway = LLMGateway(provider)
    ctx.dispatcher = _FakeDispatcher()  # type: ignore[assignment]
    return ctx


def _hyps(n: int) -> list[Hypothesis]:
    return [Hypothesis(statement=f"Hypothesis {i}", initial_confidence=0.5) for i in range(n)]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_three_hypotheses_validated_with_verdicts() -> None:
    provider = FakeLLMProvider()
    for verdict, conf in [("confirmed", 0.8), ("refuted", 0.2), ("inconclusive", 0.5)]:
        provider.queue_response(_tool_use(1))
        provider.queue_response(_verdict(verdict, conf))
    ctx = _ctx(_hyps(3), provider)

    result = await ValidationStage().execute(ctx)

    assert result.status == "succeeded"
    assert len(ctx.validated) == 3
    by_id = {v.hypothesis_id: v for v in ctx.validated}
    assert by_id["h1"].confirmed is True and by_id["h1"].confidence == "high"
    assert by_id["h2"].confirmed is False and by_id["h2"].confidence == "low"
    assert by_id["h3"].confirmed is None
    # Each carries a tool-call citation (FR-5.4).
    assert all(v.evidence and v.evidence[0].source == "get_alert_details" for v in ctx.validated)
    assert result.output == {
        "validated_count": 3, "confirmed": 1, "refuted": 1, "inconclusive": 1
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_only_top_k_are_validated() -> None:
    provider = FakeLLMProvider()
    for _ in range(3):  # only 3 hypotheses get validated (top_k=3)
        provider.queue_response(_tool_use(1))
        provider.queue_response(_verdict("confirmed", 0.7))
    ctx = _ctx(_hyps(5), provider)

    await ValidationStage().execute(ctx)
    assert len(ctx.validated) == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_budget_exhaustion_leaves_remaining_unvalidated() -> None:
    # Each call costs 0.30; cap 0.50 → after h1 (2 calls = 0.60) the budget
    # is spent and h2/h3 are recorded unvalidated.
    budget = Budget(max_llm_cost_usd=0.50)
    budget.start()
    provider = FakeLLMProvider()
    provider.queue_response(
        LLMResponse(text="", tool_calls=[{"id": "t1", "name": "get_alert_details", "input": {}}], cost_usd=0.30)
    )
    provider.queue_response(
        LLMResponse(text='{"verdict": "confirmed", "confidence": 0.9, "reasoning": "ok"}', cost_usd=0.30)
    )
    ctx = _ctx(_hyps(3), provider, budget=budget)

    await ValidationStage().execute(ctx)

    assert len(ctx.validated) == 3
    assert ctx.validated[0].validated is True
    assert ctx.validated[1].validated is False
    assert ctx.validated[2].validated is False
    assert ctx.validated[1].confidence == "medium"  # carried from initial 0.5


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_gateway_degrades() -> None:
    ctx = InvestigationContext(
        tenant=TenantContext(tenant_id=new_id(), name="t", slug="t", api_key_id=new_id()),
        investigation_id=new_id(),
        alert={}, service={}, dependencies={}, metric_catalog={}, budget=Budget(),
    )
    ctx.hypotheses = _hyps(2)
    result = await ValidationStage().execute(ctx)
    assert result.status == "succeeded"
    assert ctx.validated == []
