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

        # Spec 0007: deterministic placeholder assembled from whatever the
        # (stub) earlier stages produced. The structured-output LLM call lands
        # in spec 0012. Prefer validated hypotheses, fall back to raw ones.
        hypotheses = ctx.validated or ctx.hypotheses
        alert_name = ctx.alert.get("alert_name", "alert")
        classification = ctx.triage.classification if ctx.triage else "novel"
        headline = f"Received '{alert_name}' (triage: {classification}) — diagnosis pending real stages."

        ctx.report = Report(
            headline=headline,
            confidence="low",
            hypotheses=[
                {"statement": getattr(h, "statement", str(h))} for h in hypotheses
            ],
            prompt_version="0.0.1",
            code_version="dev",
        )
        return StageResult(
            name=self.name,
            status="succeeded",
            output={"headline": headline, "confidence": "low"},
        )
