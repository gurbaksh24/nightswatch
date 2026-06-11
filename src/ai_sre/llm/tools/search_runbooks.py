"""`search_runbooks` tool — vector search over the tenant's runbooks (FR-6.5).

Reads ``ctx.knowledge`` (injected by the orchestrator; tenant-scoped), so no
DB access and no ``core`` import here. With no knowledge base wired — or no
runbook docs — it returns an empty result list and the model proceeds without.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ai_sre.llm.tools import ToolInput, ToolOutput, ToolSpec

if TYPE_CHECKING:
    from ai_sre.core.investigation.context import InvestigationContext

_MAX_K = 10


async def search_runbooks_handler(
    input: ToolInput, ctx: InvestigationContext
) -> ToolOutput:
    """Return the runbook chunks most relevant to ``query``."""
    if ctx.knowledge is None:
        return {"results": [], "note": "knowledge base not configured"}
    query = str(input.get("query", "")).strip()
    if not query:
        return {"results": [], "note": "empty query"}
    k = min(int(input.get("k", 5)), _MAX_K)
    hits = await ctx.knowledge.search(query, k=k, kinds=["runbook"])
    return {
        "results": [
            {
                "title": h.title,
                "section": " > ".join(h.headings),
                "text": h.text,
                "score": round(h.score, 4),
            }
            for h in hits
        ]
    }


SEARCH_RUNBOOKS = ToolSpec(
    name="search_runbooks",
    description=(
        "Semantic search over the tenant's uploaded runbooks. Use it to find "
        "remediation steps or known procedures relevant to the current alert. "
        "Returns the most relevant runbook sections (may be empty)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to look for, e.g. 'database connection pool exhausted'.",
            },
            "k": {
                "type": "integer",
                "description": "Max results (default 5, max 10).",
            },
        },
        "required": ["query"],
    },
    handler=search_runbooks_handler,
    allowed_stages=frozenset({"hypothesis", "validation"}),
)
