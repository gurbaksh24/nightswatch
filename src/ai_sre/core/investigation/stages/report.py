"""ReportStage — the final structured RCA (spec 0012).

A single non-agentic LLM call constrained to a JSON schema, validated against
:class:`ReportOutput`. This is the last line of defence: it always produces
*some* report — on malformed output, budget exhaustion, or no configured
gateway it falls back to a deterministic report (``schema_version="v1-fallback"``)
built from whatever the earlier stages produced, so the user never gets
silence.

The orchestrator persists ``ctx.report`` and hands it to delivery.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from ai_sre.core.investigation.context import (
    InvestigationContext,
    Report,
    StageResult,
)
from ai_sre.exceptions import BudgetExhausted, LLMResponseInvalid
from ai_sre.llm.gateway import Message, ResponseFormat
from ai_sre.llm.prompts.report import PROMPT_VERSION, REPORT_PROMPT
from ai_sre.llm.prompts.system import SYSTEM_PROMPT


class ReportHypothesis(BaseModel):
    statement: str
    confidence: str = "low"
    confirmed: bool | None = None


class NextAction(BaseModel):
    action: str


class ReportOutput(BaseModel):
    """The structured RCA the LLM must emit (validated by the gateway)."""

    headline: str
    confidence: Literal["low", "medium", "high"] = "low"
    hypotheses: list[ReportHypothesis] = Field(default_factory=list)
    next_actions: list[NextAction] = Field(default_factory=list)
    related_incidents: list[dict[str, Any]] = Field(default_factory=list)


def _render(ctx: InvestigationContext) -> str:
    lines = [
        "## Alert",
        f"name: {ctx.alert.get('alert_name')}",
        f"severity: {ctx.alert.get('severity')}",
        f"labels: {json.dumps(ctx.alert.get('labels', {}))}",
        "",
        f"## Triage: {ctx.triage.classification if ctx.triage else 'unknown'}",
        "",
        "## Validated hypotheses",
    ]
    if ctx.validated:
        for v in ctx.validated:
            lines.append(f"- [{v.confidence}] confirmed={v.confirmed} :: {v.statement}")
            if v.reasoning:
                lines.append(f"    reasoning: {v.reasoning}")
            for e in v.evidence:
                lines.append(f"    evidence: {e.source}: {e.detail}")
    elif ctx.hypotheses:
        for h in ctx.hypotheses:
            lines.append(f"- {h.statement} (initial confidence {h.initial_confidence})")
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("Write the final RCA JSON now.")
    return "\n".join(lines)


def _fallback_report(ctx: InvestigationContext, schema_version: str) -> Report:
    """Deterministic report from whatever the earlier stages produced."""
    validated = ctx.validated or []
    confirmed = [v for v in validated if v.confirmed is True]
    if confirmed:
        headline = f"Likely cause: {confirmed[0].statement}"
        confidence = confirmed[0].confidence
    elif validated or ctx.hypotheses:
        headline = "Investigation completed without a confident root cause."
        confidence = "low"
    else:
        headline = "We received your alert but couldn't complete a diagnosis."
        confidence = "low"

    if validated:
        hyps = [
            {"statement": v.statement, "confidence": v.confidence, "confirmed": v.confirmed}
            for v in validated
        ]
    else:
        hyps = [{"statement": h.statement} for h in ctx.hypotheses]

    return Report(
        schema_version=schema_version,
        headline=headline,
        confidence=confidence,
        hypotheses=hyps,
        prompt_version=PROMPT_VERSION,
        code_version="dev",
        investigation_id=str(ctx.investigation_id),
    )


class ReportStage:
    name = "report"
    timeout_seconds = 60
    max_tool_calls = 0  # structured-output call, no agentic tool use

    async def execute(self, ctx: InvestigationContext) -> StageResult:
        # No wall assertion: the report is the last line of defence and must
        # produce something; the orchestrator's per-stage timeout still bounds it.
        if ctx.gateway is None:
            ctx.report = _fallback_report(ctx, "v1")
            return StageResult(
                name=self.name,
                status="succeeded",
                output={"confidence": ctx.report.confidence, "fallback": True},
            )

        try:
            resp = await ctx.gateway.chat(
                system=f"{SYSTEM_PROMPT}\n\n{REPORT_PROMPT}",
                messages=[Message(role="user", content=_render(ctx))],
                response_format=ResponseFormat(ReportOutput.model_json_schema()),
                budget=ctx.budget,
            )
            out = ReportOutput.model_validate(resp.structured or {})
        except (LLMResponseInvalid, ValidationError, BudgetExhausted) as exc:
            ctx.report = _fallback_report(ctx, "v1-fallback")
            return StageResult(
                name=self.name,
                status="succeeded",
                output={
                    "confidence": ctx.report.confidence,
                    "fallback": True,
                    "error": type(exc).__name__,
                },
            )

        ctx.report = Report(
            schema_version="v1",
            headline=out.headline,
            confidence=out.confidence,
            hypotheses=[h.model_dump() for h in out.hypotheses],
            next_actions=[a.model_dump() for a in out.next_actions],
            related_incidents=out.related_incidents,
            prompt_version=PROMPT_VERSION,
            code_version="dev",
            investigation_id=str(ctx.investigation_id),
        )
        return StageResult(
            name=self.name, status="succeeded", output={"confidence": out.confidence}
        )
