"""ReportStage.

Final stage. Non-agentic: a single LLM call with a structured-output schema
(tool-use mode for constrained generation). Inputs:

    * The alert
    * Validated hypotheses (with evidence)
    * Triage classification
    * Context summary
    * Past similar incidents (if any)

Output: a `Report` matching the JSON schema in `docs/04-data-model.md`.

Resilience: this stage is the last line of defence. Even if every prior stage
failed, ReportStage should produce *something* (with confidence=low,
status=partial) so the user gets a useful Slack post rather than silence.
"""

from __future__ import annotations

from ai_sre.core.investigation.context import (
    InvestigationContext,
    Report,
    StageResult,
)


class ReportStage:
    name = "report"
    timeout_seconds = 60
    max_tool_calls = 0   # structured-output call, no agentic tool use

    async def execute(self, ctx: InvestigationContext) -> StageResult:
        ctx.budget.assert_within_wall()
        # ctx.budget.assert_can_call_llm()    # we still try even on partial
        # TODO(spec-NNNN: report-stage):
        #   1. Build prompt from ctx (validated hypotheses, fallback to hypotheses,
        #      fallback to context, fallback to "we received your alert but ...").
        #   2. Call llm.chat(response_format=ReportSchema).
        #   3. Parse into Report.
        ctx.report = Report(
            headline="Stub report",
            confidence="low",
            prompt_version="0.0.1",
            code_version="dev",
        )
        return StageResult(name=self.name, status="succeeded")
