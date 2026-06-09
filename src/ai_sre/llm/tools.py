"""Tool registry + dispatcher.

A ``ToolSpec`` fully describes a tool the LLM can call. The ``ToolRegistry``
maps names to specs and filters by stage. The ``ToolDispatcher`` runs a tool
on the model's behalf: it validates input, invokes the handler, and persists a
``tool_call`` audit row (FR-5.3) via a ``ToolCallStore``.

Module boundary: this file lives in ``llm/`` and must NOT import ``core/``.
Persistence is therefore expressed as the structural ``ToolCallStore``
Protocol; the concrete tenant-scoped ``ToolCallRepository`` (in ``core/``)
satisfies it, and the composition root wires them together.

Adding a tool: define a ``ToolSpec``, ``register`` it, and (optionally) scope
it to stages via ``allowed_stages``. Real tools ship from spec 0009.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from uuid import UUID

if TYPE_CHECKING:
    from ai_sre.core.investigation.context import InvestigationContext


ToolInput = dict[str, Any]
ToolOutput = dict[str, Any]
ToolHandler = Callable[[ToolInput, "InvestigationContext"], Awaitable[ToolOutput]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    allowed_stages: frozenset[str] = field(default_factory=frozenset)


@runtime_checkable
class ToolCallStore(Protocol):
    """Persistence seam for tool-call audit rows. Implemented in ``core/`` by
    ``ToolCallRepository`` so ``llm/`` needn't import ``core/``."""

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
    ) -> None: ...


class ToolRegistry:
    """Name → ToolSpec, with a per-stage filter."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        return self._tools[name]

    def for_stage(self, stage: str) -> list[ToolSpec]:
        return [
            t
            for t in self._tools.values()
            if not t.allowed_stages or stage in t.allowed_stages
        ]

    def names(self) -> Iterable[str]:
        return self._tools.keys()


REGISTRY = ToolRegistry()


class ToolDispatcher:
    """Runs a tool the model asked for and records the call.

    ``dispatch`` never raises for tool-level failures (unknown tool, bad
    input, handler exception); it records ``outcome="error"`` and returns an
    error envelope so the model can recover. Each call (success or error) is
    persisted via the store when one is configured.
    """

    def __init__(self, registry: ToolRegistry, store: ToolCallStore | None = None) -> None:
        self.registry = registry
        self.store = store

    async def dispatch(
        self, name: str, input: dict[str, Any], ctx: InvestigationContext
    ) -> ToolOutput:
        started = time.monotonic()

        try:
            spec = self.registry.get(name)
        except KeyError:
            return await self._finish(
                ctx, name, input, started,
                output=None, outcome="error",
                error={"type": "unknown_tool", "message": f"Unknown tool {name!r}."},
            )

        missing = [k for k in spec.input_schema.get("required", []) if k not in input]
        if missing:
            return await self._finish(
                ctx, name, input, started,
                output=None, outcome="error",
                error={"type": "invalid_input", "message": f"Missing required: {missing}"},
            )

        try:
            output = await spec.handler(input, ctx)
        except Exception as exc:
            return await self._finish(
                ctx, name, input, started,
                output=None, outcome="error",
                error={"type": type(exc).__name__, "message": str(exc)},
            )

        return await self._finish(
            ctx, name, input, started, output=output, outcome="success", error=None
        )

    async def _finish(
        self,
        ctx: InvestigationContext,
        name: str,
        input: dict[str, Any],
        started: float,
        *,
        output: dict[str, Any] | None,
        outcome: str,
        error: dict[str, Any] | None,
    ) -> ToolOutput:
        latency_ms = int((time.monotonic() - started) * 1000)
        if self.store is not None:
            await self.store.record(
                investigation_id=ctx.investigation_id,
                stage_id=ctx.current_stage_id,
                tool_name=name,
                input=input,
                output=output,
                latency_ms=latency_ms,
                outcome=outcome,
                error=error,
            )
        if outcome == "success":
            return output if output is not None else {}
        return {"error": error}


# --------------------------------------------------------------------------
# Tool definitions. Handlers are implemented in their own specs (0009+).
# --------------------------------------------------------------------------


async def _query_prometheus_handler(
    input_: ToolInput, ctx: InvestigationContext
) -> ToolOutput:
    """Bridge the LLM's tool call to the Prometheus connector (spec 0009)."""
    raise NotImplementedError


def _build_query_prometheus_schema() -> dict[str, Any]:
    """JSON Schema for the LLM's `query_prometheus` tool call.

    Mirrors the QueryIntent union in connectors/base.py.
    """
    return {
        "type": "object",
        "properties": {
            "intent_kind": {
                "type": "string",
                "enum": [
                    "rate_over_window",
                    "aggregation",
                    "percentile",
                    "change_over_time",
                    "raw_promql",
                ],
            },
            "metric": {"type": "string"},
            "labels": {"type": "object", "additionalProperties": {"type": "string"}},
            "window_seconds": {"type": "integer", "minimum": 30, "maximum": 86400},
            "percentile": {"type": "number", "minimum": 0, "maximum": 1},
            "op": {"type": "string", "enum": ["sum", "avg", "max", "min", "count"]},
            "by": {"type": "array", "items": {"type": "string"}},
            "query": {"type": "string", "description": "Raw PromQL (only for raw_promql)"},
            "start": {"type": "string", "format": "date-time"},
            "end": {"type": "string", "format": "date-time"},
        },
        "required": ["intent_kind"],
    }


def register_builtin_tools() -> None:
    """Register the built-in tools. Called at app startup from spec 0009 once
    the handlers are real (the query_prometheus handler is still a stub here)."""
    REGISTRY.register(
        ToolSpec(
            name="query_prometheus",
            description=(
                "Run a typed query against the tenant's Prometheus. Choose "
                "`intent_kind` and fill the corresponding fields."
            ),
            input_schema=_build_query_prometheus_schema(),
            handler=_query_prometheus_handler,
            allowed_stages=frozenset({"hypothesis", "validation"}),
        )
    )
