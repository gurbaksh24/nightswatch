"""`get_service_dependencies` tool — the subject service's topology (FR-6.3).

Reads ``ctx.dependencies`` (pre-loaded by the orchestrator), so no DB access
and no ``core`` import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ai_sre.llm.tools import ToolInput, ToolOutput, ToolSpec

if TYPE_CHECKING:
    from ai_sre.core.investigation.context import InvestigationContext


async def get_service_dependencies_handler(
    _input: ToolInput, ctx: InvestigationContext
) -> ToolOutput:
    """Return upstream/downstream dependency edges of the subject service."""
    deps = ctx.dependencies or {}
    return {
        "upstream": deps.get("upstream", []),
        "downstream": deps.get("downstream", []),
    }


GET_SERVICE_DEPENDENCIES = ToolSpec(
    name="get_service_dependencies",
    description="Return the subject service's discovered upstream/downstream dependencies.",
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=get_service_dependencies_handler,
    allowed_stages=frozenset({"hypothesis", "validation"}),
)
