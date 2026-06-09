"""ValidationStage.

Agentic: for the top-K hypotheses (default 3), prompt the LLM to design a
test that would confirm or refute each, run the resulting queries, and
update the hypothesis confidence based on the result.

For each hypothesis the LLM is asked:

    "What single query (or small set of queries) would confirm or refute
     this hypothesis? Output a tool call. Then, based on the result, give
     a confidence in [0,1] and one-paragraph reasoning."

Outputs: `ctx.validated: list[ValidatedHypothesis]`. The Report stage
re-ranks based on these confidences.
"""

from __future__ import annotations

from ai_sre.core.investigation.context import InvestigationContext, StageResult


class ValidationStage:
    name = "validation"
    timeout_seconds = 90
    max_tool_calls = 10  # per investigation, total across hypotheses

    top_k_to_validate: int = 3

    async def execute(self, ctx: InvestigationContext) -> StageResult:
        ctx.budget.assert_within_wall()
        # Spec 0007: no-op. The agentic per-hypothesis validation tool-loop
        # lands in spec 0011; until then ctx.validated stays empty and the
        # report falls back to the raw hypotheses.
        return StageResult(
            name=self.name,
            status="succeeded",
            output={"validated_count": len(ctx.validated)},
        )
