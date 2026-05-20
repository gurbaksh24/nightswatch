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

from ai_sre.core.investigation.context import InvestigationContext, StageResult


class HypothesisStage:
    name = "hypothesis"
    timeout_seconds = 90
    max_tool_calls = 15

    async def execute(self, ctx: InvestigationContext) -> StageResult:
        ctx.budget.assert_within_wall()
        ctx.budget.assert_can_call_llm()

        # TODO(spec-NNNN: hypothesis-stage):
        #   1. system = HYPOTHESIS_SYSTEM_PROMPT (from llm/prompts/hypothesis.py)
        #   2. user   = render(ctx.alert, ctx.context, ctx.service, ctx.dependencies)
        #   3. tools  = llm.tools.for_stage("hypothesis")
        #   4. result = await llm.tool_loop(system, user, tools, dispatcher, budget)
        #   5. ctx.hypotheses = parse_hypotheses(result.final_message)

        return StageResult(name=self.name, status="succeeded")
