"""Tool registry.

A `ToolSpec` is the full description of one tool the LLM can call. The
registry maps names to specs and provides a per-stage filter.

Adding a tool:

    REGISTRY.register(ToolSpec(
        name="query_prometheus",
        description="Run a typed PromQL query against the tenant's Prometheus.",
        input_schema={...JSON Schema...},
        handler=my_handler,
        allowed_stages={"hypothesis", "validation"},
    ))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Iterable

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


class _Registry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        return self._tools[name]

    def for_stage(self, stage: str) -> list[ToolSpec]:
        return [t for t in self._tools.values() if not t.allowed_stages or stage in t.allowed_stages]

    def names(self) -> Iterable[str]:
        return self._tools.keys()


REGISTRY = _Registry()


class ToolDispatcher:
    """Routes a tool-call request from the LLM to the right handler."""

    def __init__(self, registry: _Registry = REGISTRY) -> None:
        self.registry = registry

    async def dispatch(
        self, tool_call: dict[str, Any], ctx: InvestigationContext
    ) -> ToolOutput:
        name = tool_call.get("name")
        if not name:
            raise ValueError("tool_call missing 'name'")
        spec = self.registry.get(name)
        input_ = tool_call.get("input", {})
        # TODO(spec-NNNN: tool-dispatcher):
        #   - jsonschema validate input against spec.input_schema
        #   - call spec.handler(input_, ctx)
        #   - on exception, return structured error so LLM can retry
        return await spec.handler(input_, ctx)


# --------------------------------------------------------------------------
# Tool definitions. Each handler is implemented as part of its own spec.
# --------------------------------------------------------------------------


async def _query_prometheus_handler(
    input_: ToolInput, ctx: InvestigationContext
) -> ToolOutput:
    """Bridge the LLM's tool call to the Prometheus connector.

    See `connectors/prometheus/queries.py` for the typed intent → PromQL map.
    """
    # TODO(spec-NNNN: tool-query-prometheus)
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
    """Register the built-in tools. Called at app startup."""
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
    # TODO: register list_metric_names, get_service_dependencies,
    #       get_recent_changes, search_runbooks, search_past_incidents,
    #       get_alert_details — one spec each.
