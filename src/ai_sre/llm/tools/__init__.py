"""Tool registry + dispatcher, and the concrete-tool registration entrypoint.

A ``ToolSpec`` fully describes a tool the LLM can call. The ``ToolRegistry``
maps names to specs and filters by stage. The ``ToolDispatcher`` runs a tool
on the model's behalf: validate input, invoke the handler, and persist a
``tool_call`` audit row (FR-5.3) via a ``ToolCallStore``.

Module boundary: this package lives in ``llm/`` and must NOT import ``core/``.
Persistence is expressed as the structural ``ToolCallStore`` Protocol (the
concrete ``ToolCallRepository`` in ``core/`` satisfies it). Concrete tools
(``query_prometheus.py`` etc.) read what they need off the
``InvestigationContext`` the orchestrator pre-populates, or use
``ctx.connector_registry`` — never a ``core`` import.
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
    """Persistence seam for tool-call audit rows. ``ToolCallRepository`` in
    ``core/`` satisfies this so ``llm/`` needn't import ``core/``."""

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
    """Name -> ToolSpec, with a per-stage filter."""

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
    error envelope so the model can recover.
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


def register_builtin_tools(registry: ToolRegistry = REGISTRY) -> None:
    """Register the built-in tool set. Idempotent — safe to call per
    investigation. Imports are local to avoid an import cycle (the tool
    modules import ``ToolSpec`` from this package)."""
    from ai_sre.llm.tools.get_alert_details import GET_ALERT_DETAILS
    from ai_sre.llm.tools.get_service_dependencies import GET_SERVICE_DEPENDENCIES
    from ai_sre.llm.tools.list_metric_names import LIST_METRIC_NAMES
    from ai_sre.llm.tools.query_prometheus import QUERY_PROMETHEUS

    existing = set(registry.names())
    for spec in (
        QUERY_PROMETHEUS,
        LIST_METRIC_NAMES,
        GET_SERVICE_DEPENDENCIES,
        GET_ALERT_DETAILS,
    ):
        if spec.name not in existing:
            registry.register(spec)
