"""HypothesisStage.

Agentic: an LLM tool-loop. Goal: produce a ranked list of up to N hypotheses
(default 5) for what's causing this alert, each with at least one piece of
initial evidence.

Tools available to the LLM at this stage:
    * `query_prometheus` — typed query intent.
    * `list_metric_names` — search the per-tenant metric catalog.
    * `get_service_dependencies` — read the topology.
    * `search_past_incidents` — vector search of past investigations.
    * `search_runbooks` — vector search of uploaded runbooks.

The stage:
    1. Builds a system prompt (hypothesis.py prompts).
    2. Calls `llm_gateway.tool_loop(...)` with the budget.
    3. Parses the final response into `Hypothesis` objects.
    4. Writes them to `ctx.hypotheses`.

If the LLM runs out of budget mid-loop, partial hypotheses are still useful;
keep them.
"""

from __future__ import annotations

from ai_sre.core.investigation.context import (
    Hypothesis,
    InvestigationContext,
    StageResult,
)


class HypothesisStage:
    name = "hypothesis"
    timeout_seconds = 90
    max_tool_calls = 15

    async def execute(self, ctx: InvestigationContext) -> StageResult:
        ctx.budget.assert_within_wall()

        # Spec 0007: deterministic placeholder. The real agentic tool-loop
        # (system prompt -> llm.tool_loop -> parse hypotheses) lands in spec
        # 0009. We emit one placeholder so the rest of the pipeline has
        # something to carry through.
        ctx.hypotheses = [
            Hypothesis(
                statement="Placeholder hypothesis (LLM hypothesis stage lands in spec 0009).",
                initial_confidence=0.0,
            )
        ]
        return StageResult(
            name=self.name,
            status="succeeded",
            output={"hypothesis_count": len(ctx.hypotheses)},
        )
