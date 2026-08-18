"""`query_newrelic` tool — run a vetted NRQL query against the tenant's
New Relic account (spec 0019).

Mirrors ``query_prometheus``: the LLM picks a typed ``intent_kind`` (the same
generic intents, translated to NRQL by the connector) or the ``raw_nrql``
escape hatch (validated for safety). This handler imports only
``connectors.base`` and reaches the connector via ``ctx.connector_registry``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from ai_sre.connectors.base import (
    Aggregation,
    ChangeOverTime,
    ConnectorKind,
    NRQLQuery,
    Percentile,
    QueryIntent,
    RateOverWindow,
    RawNRQL,
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

# Rows handed back to the model (the connector already caps at max_series;
# this keeps the LLM context small).
_MAX_ROWS_TO_MODEL = 25


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_intent(input_: ToolInput) -> QueryIntent | RawNRQL | None:
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
    if kind == "raw_nrql":
        return RawNRQL(query=input_.get("query", ""))
    return None


async def query_newrelic_handler(input_: ToolInput, ctx: InvestigationContext) -> ToolOutput:
    """Execute a typed New Relic query and return trimmed result rows."""
    if ctx.connector_registry is None:
        return {"error": "no_connector", "detail": "No connector registry available."}

    intent = _build_intent(input_)
    if intent is None:
        return {
            "error": "invalid_intent",
            "detail": f"Unknown intent_kind: {input_.get('intent_kind')!r}",
        }

    try:
        connector = await ctx.connector_registry.get(ctx.tenant.tenant_id, ConnectorKind.NEWRELIC)
    except (IntegrationNotFound, IntegrationUnhealthy, IntegrationError) as exc:
        return {"error": "no_newrelic", "detail": str(exc)}

    query = NRQLQuery(
        intent=intent,
        start=_parse_dt(input_.get("start")),
        end=_parse_dt(input_.get("end")),
    )
    try:
        result = await connector.query(query)
    except ConnectorError as exc:
        # Includes timeouts and rejected raw NRQL — surface to the model so
        # it can narrow the window or pick a different query.
        return {"error": "query_failed", "detail": str(exc)}

    if not result.success:
        return {"error": "query_failed", "detail": result.error}

    rows = result.data.get("results", []) or []
    trimmed = rows[:_MAX_ROWS_TO_MODEL]
    return {
        "nrql": result.data.get("nrql"),
        "results": trimmed,
        "series_count": result.series_count,
        "truncated": bool(result.data.get("truncated")) or len(rows) > _MAX_ROWS_TO_MODEL,
    }


QUERY_NEWRELIC = ToolSpec(
    name="query_newrelic",
    description=(
        "Run a query against the tenant's New Relic account (NRQL). Choose "
        "`intent_kind` and fill the matching fields, or pass `raw_nrql` "
        "(validated for safety). Only available when the tenant has New Relic "
        "connected."
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
                    "raw_nrql",
                ],
            },
            "metric": {"type": "string"},
            "labels": {"type": "object", "additionalProperties": {"type": "string"}},
            "window_seconds": {"type": "integer", "minimum": 30, "maximum": 86400},
            "percentile": {"type": "number", "minimum": 0, "maximum": 1},
            "op": {"type": "string", "enum": ["sum", "avg", "max", "min", "count"]},
            "by": {"type": "array", "items": {"type": "string"}},
            "query": {"type": "string", "description": "Raw NRQL (only for raw_nrql)"},
            "start": {"type": "string", "format": "date-time"},
            "end": {"type": "string", "format": "date-time"},
        },
        "required": ["intent_kind"],
    },
    handler=query_newrelic_handler,
    allowed_stages=frozenset({"hypothesis", "validation"}),
)
