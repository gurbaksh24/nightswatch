"""`search_past_incidents` tool — vector search over past investigations (FR-6.6).

Completed investigations are auto-ingested as ``kind="past_investigation"``
docs (FR-7.3, orchestrator finalize). This tool lets the model ask "have we
seen something like this before?" during hypothesis and validation.

Reads ``ctx.knowledge`` (injected by the orchestrator; tenant-scoped) — no
``core`` import here. Degrades to an empty result list when nothing is wired
or nothing matches.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ai_sre.llm.tools import ToolInput, ToolOutput, ToolSpec

if TYPE_CHECKING:
    from ai_sre.core.investigation.context import InvestigationContext

_MAX_K = 10


async def search_past_incidents_handler(
    input: ToolInput, ctx: InvestigationContext
) -> ToolOutput:
    """Return past-incident chunks most relevant to ``query``."""
    if ctx.knowledge is None:
        return {"results": [], "note": "knowledge base not configured"}
    query = str(input.get("query", "")).strip()
    if not query:
        return {"results": [], "note": "empty query"}
    k = min(int(input.get("k", 5)), _MAX_K)
    hits = await ctx.knowledge.search(query, k=k, kinds=["past_investigation"])
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


SEARCH_PAST_INCIDENTS = ToolSpec(
    name="search_past_incidents",
    description=(
        "Semantic search over this tenant's previously completed "
        "investigations (past incidents and their root causes). Use it to "
        "check whether a similar incident happened before and what the "
        "diagnosis was. Returns the most relevant excerpts (may be empty)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Describe the incident, e.g. 'checkout latency spike after deploy'.",
            },
            "k": {
                "type": "integer",
                "description": "Max results (default 5, max 10).",
            },
        },
        "required": ["query"],
    },
    handler=search_past_incidents_handler,
    allowed_stages=frozenset({"hypothesis", "validation"}),
)
