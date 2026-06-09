"""Unit tests for LLMGateway (spec 0008). No real provider — scripted fakes."""

from __future__ import annotations

from typing import Any

import pytest

from ai_sre.core.investigation.budget import Budget
from ai_sre.core.investigation.context import InvestigationContext
from ai_sre.core.tenant.context import TenantContext
from ai_sre.exceptions import LLMResponseInvalid, LLMTransientError
from ai_sre.llm.gateway import LLMGateway, LLMProvider, LLMResponse, Message, ResponseFormat
from ai_sre.llm.tools import ToolDispatcher, ToolRegistry, ToolSpec
from ai_sre.utils.ids import new_id


class _ScriptedProvider(LLMProvider):
    """Pops queued responses; an Exception entry is raised instead."""

    def __init__(self, script: list[LLMResponse | Exception]) -> None:
        self._script = list(script)
        self.calls = 0

    async def chat(self, *, system, messages, tools, response_format, max_tokens):  # type: ignore[no-untyped-def]
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def estimate_cost(self, tokens: int) -> float:
        return 0.0


def _budget(**kw: object) -> Budget:
    defaults: dict[str, object] = {
        "max_wall_seconds": 300,
        "max_tool_calls": 30,
        "max_llm_tokens": 1_000_000,
        "max_llm_cost_usd": 100.0,
    }
    defaults.update(kw)
    return Budget(**defaults)  # type: ignore[arg-type]


def _ctx(budget: Budget) -> InvestigationContext:
    return InvestigationContext(
        tenant=TenantContext(tenant_id=new_id(), name="t", slug="t", api_key_id=new_id()),
        investigation_id=new_id(),
        alert={},
        service={},
        dependencies={},
        metric_catalog={},
        budget=budget,
    )


# ---- chat: budget + retry ----


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_records_budget() -> None:
    provider = _ScriptedProvider([LLMResponse(text="hi", tokens_used=120, cost_usd=0.02)])
    gateway = LLMGateway(provider)
    budget = _budget()
    resp = await gateway.chat(system="s", messages=[Message(role="user", content="hi")], budget=budget)
    assert resp.text == "hi"
    assert budget.tokens_used == 120
    assert budget.cost_used_usd == pytest.approx(0.02)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_retries_then_succeeds_on_transient() -> None:
    provider = _ScriptedProvider(
        [LLMTransientError("429"), LLMTransientError("503"), LLMResponse(text="ok")]
    )
    gateway = LLMGateway(provider, max_attempts=3)
    resp = await gateway.chat(system="s", messages=[Message(role="user", content="x")], budget=_budget())
    assert resp.text == "ok"
    assert provider.calls == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_raises_when_retries_exhausted() -> None:
    provider = _ScriptedProvider([LLMTransientError("a"), LLMTransientError("b")])
    gateway = LLMGateway(provider, max_attempts=2)
    with pytest.raises(LLMTransientError):
        await gateway.chat(system="s", messages=[Message(role="user", content="x")], budget=_budget())
    assert provider.calls == 2


# ---- chat: structured output ----


@pytest.mark.unit
@pytest.mark.asyncio
async def test_structured_output_parsed() -> None:
    provider = _ScriptedProvider([LLMResponse(text='{"headline": "x", "confidence": "low"}')])
    gateway = LLMGateway(provider)
    resp = await gateway.chat(
        system="s",
        messages=[Message(role="user", content="x")],
        response_format=ResponseFormat(json_schema={}),
        budget=_budget(),
    )
    assert resp.structured == {"headline": "x", "confidence": "low"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_structured_output_retries_once_then_succeeds() -> None:
    provider = _ScriptedProvider(
        [LLMResponse(text="not json at all"), LLMResponse(text='{"ok": true}')]
    )
    gateway = LLMGateway(provider)
    resp = await gateway.chat(
        system="s",
        messages=[Message(role="user", content="x")],
        response_format=ResponseFormat(json_schema={}),
        budget=_budget(),
    )
    assert resp.structured == {"ok": True}
    assert provider.calls == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_structured_output_invalid_after_retry_raises() -> None:
    provider = _ScriptedProvider([LLMResponse(text="nope"), LLMResponse(text="still nope")])
    gateway = LLMGateway(provider)
    with pytest.raises(LLMResponseInvalid):
        await gateway.chat(
            system="s",
            messages=[Message(role="user", content="x")],
            response_format=ResponseFormat(json_schema={}),
            budget=_budget(),
        )


# ---- tool_loop ----


def _registry_with_ping(flag: list[bool]) -> ToolRegistry:
    async def _ping(_input: dict[str, Any], _ctx: InvestigationContext) -> dict[str, Any]:
        flag.append(True)
        return {"pong": True}

    reg = ToolRegistry()
    reg.register(ToolSpec(name="ping", description="ping", input_schema={}, handler=_ping))
    return reg


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tool_loop_returns_when_no_tool_calls() -> None:
    provider = _ScriptedProvider([LLMResponse(text="done")])
    gateway = LLMGateway(provider)
    reg = ToolRegistry()
    resp = await gateway.tool_loop(
        system="s", user="u", tools=[], dispatcher=ToolDispatcher(reg),
        ctx=_ctx(_budget()), budget=_budget(),
    )
    assert resp.text == "done"
    assert provider.calls == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tool_loop_runs_tool_then_terminates() -> None:
    ran: list[bool] = []
    reg = _registry_with_ping(ran)
    provider = _ScriptedProvider(
        [
            LLMResponse(text="", tool_calls=[{"id": "t1", "name": "ping", "input": {}}]),
            LLMResponse(text="final"),
        ]
    )
    gateway = LLMGateway(provider)
    budget = _budget()
    resp = await gateway.tool_loop(
        system="s", user="u", tools=reg.for_stage("hypothesis"),
        dispatcher=ToolDispatcher(reg), ctx=_ctx(budget), budget=budget,
    )
    assert resp.text == "final"
    assert provider.calls == 2
    assert ran == [True]  # the tool ran exactly once
    assert budget.tool_calls_used == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tool_loop_stops_when_tool_budget_exhausted() -> None:
    ran: list[bool] = []
    reg = _registry_with_ping(ran)
    provider = _ScriptedProvider(
        [LLMResponse(text="", tool_calls=[{"id": "t1", "name": "ping", "input": {}}])]
    )
    gateway = LLMGateway(provider)
    budget = _budget(max_tool_calls=0)  # can't run any tool
    resp = await gateway.tool_loop(
        system="s", user="u", tools=reg.for_stage("hypothesis"),
        dispatcher=ToolDispatcher(reg), ctx=_ctx(budget), budget=budget,
    )
    # Loop broke before dispatching the tool; returns the last response.
    assert resp.tool_calls
    assert ran == []
