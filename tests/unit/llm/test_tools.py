"""Unit tests for ToolDispatcher (spec 0008). Uses an in-memory store fake."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from ai_sre.core.investigation.context import InvestigationContext
from ai_sre.core.tenant.context import TenantContext
from ai_sre.llm.tools import ToolDispatcher, ToolRegistry, ToolSpec
from ai_sre.utils.ids import new_id


class _FakeStore:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(
        self,
        *,
        investigation_id: UUID,
        stage_id: UUID | None,
        tool_name: str,
        input: dict[str, Any],
        output: dict[str, Any] | None,
        latency_ms: int,
        outcome: str,
        error: dict[str, Any] | None,
    ) -> None:
        self.records.append(
            {
                "investigation_id": investigation_id,
                "stage_id": stage_id,
                "tool_name": tool_name,
                "input": input,
                "output": output,
                "outcome": outcome,
                "error": error,
            }
        )


def _ctx() -> InvestigationContext:
    from ai_sre.core.investigation.budget import Budget

    ctx = InvestigationContext(
        tenant=TenantContext(tenant_id=new_id(), name="t", slug="t", api_key_id=new_id()),
        investigation_id=new_id(),
        alert={},
        service={},
        dependencies={},
        metric_catalog={},
        budget=Budget(),
    )
    ctx.current_stage_id = new_id()
    return ctx


def _registry(spec: ToolSpec) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(spec)
    return reg


@pytest.mark.unit
@pytest.mark.asyncio
async def test_success_records_and_returns_output() -> None:
    async def _echo(input: dict[str, Any], _ctx: InvestigationContext) -> dict[str, Any]:
        return {"echoed": input.get("v")}

    reg = _registry(ToolSpec(name="echo", description="", input_schema={}, handler=_echo))
    store = _FakeStore()
    ctx = _ctx()
    out = await ToolDispatcher(reg, store).dispatch("echo", {"v": 7}, ctx)

    assert out == {"echoed": 7}
    assert len(store.records) == 1
    rec = store.records[0]
    assert rec["outcome"] == "success"
    assert rec["output"] == {"echoed": 7}
    assert rec["stage_id"] == ctx.current_stage_id


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_tool_records_error() -> None:
    store = _FakeStore()
    out = await ToolDispatcher(ToolRegistry(), store).dispatch("nope", {}, _ctx())
    assert "error" in out
    assert store.records[0]["outcome"] == "error"
    assert store.records[0]["error"]["type"] == "unknown_tool"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_required_input_skips_handler() -> None:
    called: list[bool] = []

    async def _needs_x(input: dict[str, Any], _ctx: InvestigationContext) -> dict[str, Any]:
        called.append(True)
        return {}

    reg = _registry(
        ToolSpec(
            name="needs_x",
            description="",
            input_schema={"type": "object", "required": ["x"]},
            handler=_needs_x,
        )
    )
    store = _FakeStore()
    out = await ToolDispatcher(reg, store).dispatch("needs_x", {}, _ctx())

    assert "error" in out
    assert called == []  # handler never invoked
    assert store.records[0]["error"]["type"] == "invalid_input"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handler_exception_records_error() -> None:
    async def _boom(_input: dict[str, Any], _ctx: InvestigationContext) -> dict[str, Any]:
        raise RuntimeError("kaboom")

    reg = _registry(ToolSpec(name="boom", description="", input_schema={}, handler=_boom))
    store = _FakeStore()
    out = await ToolDispatcher(reg, store).dispatch("boom", {}, _ctx())

    assert out["error"]["type"] == "RuntimeError"
    assert store.records[0]["outcome"] == "error"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_without_store_is_ok() -> None:
    async def _ok(_input: dict[str, Any], _ctx: InvestigationContext) -> dict[str, Any]:
        return {"ok": True}

    reg = _registry(ToolSpec(name="ok", description="", input_schema={}, handler=_ok))
    out = await ToolDispatcher(reg, store=None).dispatch("ok", {}, _ctx())
    assert out == {"ok": True}
