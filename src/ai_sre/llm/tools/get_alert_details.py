"""`get_alert_details` tool — returns the triggering alert from context (FR-6.7)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ai_sre.llm.tools import ToolInput, ToolOutput, ToolSpec

if TYPE_CHECKING:
    from ai_sre.core.investigation.context import InvestigationContext


async def get_alert_details_handler(
    _input: ToolInput, ctx: InvestigationContext
) -> ToolOutput:
    """Return the parsed triggering alert (name, labels, severity, status)."""
    return {"alert": ctx.alert}


GET_ALERT_DETAILS = ToolSpec(
    name="get_alert_details",
    description="Return the triggering alert: name, labels, severity, status.",
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=get_alert_details_handler,
    allowed_stages=frozenset({"hypothesis", "validation"}),
)
