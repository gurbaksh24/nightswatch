"""`list_metric_names` tool — search the per-tenant metric catalog (FR-6.2).

Reads the catalog the orchestrator pre-loaded into ``ctx.metric_catalog`` (so
this tool needs no DB access and no ``core`` import).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ai_sre.llm.tools import ToolInput, ToolOutput, ToolSpec

if TYPE_CHECKING:
    from ai_sre.core.investigation.context import InvestigationContext

_MAX_RESULTS = 200


async def list_metric_names_handler(
    input_: ToolInput, ctx: InvestigationContext
) -> ToolOutput:
    """Return catalog metric names, optionally filtered by a substring."""
    metrics = ctx.metric_catalog.get("metrics", []) if ctx.metric_catalog else []
    names = [m["metric_name"] for m in metrics if "metric_name" in m]

    filt = (input_.get("filter") or "").strip().lower()
    if filt:
        names = [n for n in names if filt in n.lower()]

    return {
        "metric_names": names[:_MAX_RESULTS],
        "total": len(names),
        "truncated": len(names) > _MAX_RESULTS,
    }


LIST_METRIC_NAMES = ToolSpec(
    name="list_metric_names",
    description=(
        "List metric names from the service's catalog. Optionally pass "
        "`filter` to substring-match (case-insensitive)."
    ),
    input_schema={
        "type": "object",
        "properties": {"filter": {"type": "string"}},
        "required": [],
    },
    handler=list_metric_names_handler,
    allowed_stages=frozenset({"hypothesis", "validation"}),
)
