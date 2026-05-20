"""TriageStage.

First gate. Mostly deterministic; LLM only on ambiguity.

Classification: `noise` | `known_issue` | `novel`.

- `noise`     → flapping alert / known transient. Short-circuit to a low-cost
                report ("we see this alert is flapping; nothing to investigate").
- `known_issue` → similar alert in the last N hours has an associated past
                  investigation. Report leans heavily on the prior conclusion.
- `novel`     → proceed with the full pipeline.
"""

from __future__ import annotations

from ai_sre.core.investigation.context import (
    InvestigationContext,
    StageResult,
    TriageResult,
)


class TriageStage:
    name = "triage"
    timeout_seconds = 30
    max_tool_calls = 5

    async def execute(self, ctx: InvestigationContext) -> StageResult:
        ctx.budget.assert_within_wall()
        # TODO(spec-NNNN: triage-stage):
        #   1. Deterministic: check the last 24h for matching fingerprints.
        #   2. Deterministic: flapping detection (state-change rate over window).
        #   3. If ambiguous, single small LLM call to classify.
        #   4. Populate ctx.triage.
        ctx.triage = TriageResult(classification="novel", reasoning="stub")
        return StageResult(name=self.name, status="succeeded", output={"classification": "novel"})
