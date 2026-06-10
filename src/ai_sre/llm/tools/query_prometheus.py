"""`query_prometheus` tool — run a vetted query against the tenant's Prometheus.

The LLM never writes raw PromQL into our HTTP body: it picks a typed
``intent_kind`` and fills fields, which we translate to a ``QueryIntent``. The
connector does the PromQL build + safety validation (NFR-5.6), so this handler
imports only ``connectors.base`` (allowed from ``llm/``) and reaches the
connector via ``ctx.connector_registry``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from ai_sre.connectors.base import (
    Aggregation,
    ChangeOverTime,
    ConnectorKind,
    Percentile,
    PromQLQuery,
    QueryIntent,
    RateOverWindow,
    RawPromQL,
)
from ai_sre.exceptions import (
    ConnectorError,
    IntegrationError,
    IntegrationNotFound,
    IntegrationUnhealthy,
)
from ai_sre.llm.tools import ToolInput, ToolOutput, ToolSpec

if TYPE_CHECKING:
    from ai_sre.core.investigation.context import InvestigationContext

# Series cap on the payload handed back to the model (the connector already
# caps at max_series; this keeps the LLM context small).
_MAX_SERIES_TO_MODEL = 25


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_intent(input_: ToolInput) -> QueryIntent | None:
    kind = input_.get("intent_kind")
    metric = input_.get("metric", "")
    labels = input_.get("labels") or {}
    window = int(input_.get("window_seconds", 300))

    if kind == "rate_over_window":
        return RateOverWindow(metric=metric, labels=labels, window_seconds=window)
    if kind == "change_over_time":
        return ChangeOverTime(metric=metric, labels=labels, window_seconds=window)
    if kind == "percentile":
        return Percentile(
            p=float(input_.get("percentile", 0.95)),
            metric=metric,
            labels=labels,
            window_seconds=window,
        )
    if kind == "aggregation":
        return Aggregation(
            op=input_.get("op", "sum"),
            by=tuple(input_.get("by", []) or ()),
            inner=RateOverWindow(metric=metric, labels=labels, window_seconds=window),
        )
    if kind == "raw_promql":
        return RawPromQL(query=input_.get("query", ""))
    return None


async def query_prometheus_handler(
    input_: ToolInput, ctx: InvestigationContext
) -> ToolOutput:
    """Execute a typed Prometheus query and return trimmed samples."""
    if ctx.connector_registry is None:
        return {"error": "no_connector", "detail": "No connector registry available."}

    intent = _build_intent(input_)
    if intent is None:
        return {"error": "invalid_intent", "detail": f"Unknown intent_kind: {input_.get('intent_kind')!r}"}

    try:
        connector = await ctx.connector_registry.get(
            ctx.tenant.tenant_id, ConnectorKind.PROMETHEUS
        )
    except (IntegrationNotFound, IntegrationUnhealthy, IntegrationError) as exc:
        return {"error": "no_prometheus", "detail": str(exc)}

    query = PromQLQuery(
        intent=intent,
        start=_parse_dt(input_.get("start")),
        end=_parse_dt(input_.get("end")),
    )
    try:
        result = await connector.query(query)
    except ConnectorError as exc:
        # Includes timeouts and rejected/unsafe raw PromQL — surface to the
        # model so it can narrow the window or pick a different query.
        return {"error": "query_failed", "detail": str(exc)}

    if not result.success:
        return {"error": "query_failed", "detail": result.error}

    series = result.data.get("result", []) or []
    trimmed = series[:_MAX_SERIES_TO_MODEL]
    return {
        "promql": result.data.get("promql"),
        "result": trimmed,
        "series_count": result.series_count,
        "truncated": bool(result.data.get("truncated")) or len(series) > _MAX_SERIES_TO_MODEL,
    }


QUERY_PROMETHEUS = ToolSpec(
    name="query_prometheus",
    description=(
        "Run a query against the tenant's Prometheus. Choose `intent_kind` and "
        "fill the matching fields, or pass `raw_promql` (validated for safety)."
    ),
    input_schema={
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
    },
    handler=query_prometheus_handler,
    allowed_stages=frozenset({"hypothesis", "validation"}),
)
