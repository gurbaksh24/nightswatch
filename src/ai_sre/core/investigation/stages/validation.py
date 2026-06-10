"""ValidationStage — confirm/refute the top-K hypotheses (spec 0011).

For each of the top-K hypotheses (default 3) the model is asked to design a
single confirming/refuting query, run it via the tools, and return a verdict +
confidence. Each hypothesis gets its own iteration cap (a sub-budget) so one
vague hypothesis can't burn the whole stage; if the global budget runs out
mid-stage, the remaining hypotheses are recorded unvalidated at their
Hypothesis-stage confidence.

Output: ``ctx.validated: list[ValidatedHypothesis]`` — every claim carries the
tool calls that back it (FR-5.4). Degrades to empty when no gateway is
configured.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ai_sre.core.investigation.context import (
    Evidence,
    Hypothesis,
    InvestigationContext,
    StageResult,
    ValidatedHypothesis,
)
from ai_sre.exceptions import BudgetExhausted
from ai_sre.llm.gateway import LLMResponse
from ai_sre.llm.prompts.system import SYSTEM_PROMPT
from ai_sre.llm.prompts.validation import VALIDATION_PROMPT
from ai_sre.llm.tools import REGISTRY


def _bucket(confidence: float) -> str:
    if confidence >= 0.67:
        return "high"
    if confidence >= 0.34:
        return "medium"
    return "low"


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[len("json"):]
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        try:
            obj = json.loads(match.group(0))
        except (ValueError, TypeError):
            return {}
    return obj if isinstance(obj, dict) else {}


def _evidence_from_calls(resp: LLMResponse) -> list[Evidence]:
    evidence: list[Evidence] = []
    for call in resp.dispatched_tool_calls:
        name = str(call.get("name", "tool"))
        detail = json.dumps(call.get("result", {}), default=str)[:200]
        evidence.append(Evidence(source=name, detail=detail))
    return evidence


def _to_validated(
    hypothesis_id: str, hypothesis: Hypothesis, resp: LLMResponse
) -> ValidatedHypothesis:
    parsed = _extract_json(resp.text)
    verdict = parsed.get("verdict")
    confirmed = {"confirmed": True, "refuted": False}.get(
        verdict if isinstance(verdict, str) else ""
    )
    conf_raw = parsed.get("confidence", hypothesis.initial_confidence)
    confidence = (
        float(conf_raw)
        if isinstance(conf_raw, (int, float))
        else hypothesis.initial_confidence
    )
    reasoning = str(parsed.get("reasoning", ""))

    evidence = _evidence_from_calls(resp)
    if not evidence and reasoning:
        evidence = [Evidence(source="reasoning", detail=reasoning)]

    return ValidatedHypothesis(
        hypothesis_id=hypothesis_id,
        statement=hypothesis.statement,
        confidence=_bucket(confidence),
        confirmed=confirmed,
        evidence=evidence,
        reasoning=reasoning,
        validated=True,
    )


def _unvalidated(hypothesis_id: str, hypothesis: Hypothesis) -> ValidatedHypothesis:
    return ValidatedHypothesis(
        hypothesis_id=hypothesis_id,
        statement=hypothesis.statement,
        confidence=_bucket(hypothesis.initial_confidence),
        confirmed=None,
        evidence=[],
        reasoning="Not validated — investigation budget exhausted.",
        validated=False,
    )


class ValidationStage:
    name = "validation"
    timeout_seconds = 90
    max_tool_calls = 10
    top_k = 3
    max_iterations_per_hypothesis = 4

    async def execute(self, ctx: InvestigationContext) -> StageResult:
        ctx.budget.assert_within_wall()

        if ctx.gateway is None or ctx.dispatcher is None:
            return StageResult(
                name=self.name, status="succeeded", output={"skipped": "no_gateway"}
            )

        hypotheses = ctx.hypotheses[: self.top_k]
        tools = REGISTRY.for_stage(self.name)
        validated: list[ValidatedHypothesis] = []

        for i, hypothesis in enumerate(hypotheses):
            hid = f"h{i + 1}"
            # Per-hypothesis budget guard: if the investigation is out of
            # budget, record this and all remaining as unvalidated and stop.
            try:
                ctx.budget.assert_can_call_llm()
            except BudgetExhausted:
                validated.extend(
                    _unvalidated(f"h{j + 1}", hypotheses[j])
                    for j in range(i, len(hypotheses))
                )
                break

            user = VALIDATION_PROMPT.format(
                hypothesis_statement=hypothesis.statement,
                initial_confidence=hypothesis.initial_confidence,
            )
            resp = await ctx.gateway.tool_loop(
                system=SYSTEM_PROMPT,
                user=user,
                tools=tools,
                dispatcher=ctx.dispatcher,
                ctx=ctx,
                budget=ctx.budget,
                max_iterations=self.max_iterations_per_hypothesis,
            )
            validated.append(_to_validated(hid, hypothesis, resp))

        ctx.validated = validated
        confirmed = sum(1 for v in validated if v.confirmed is True)
        refuted = sum(1 for v in validated if v.confirmed is False)
        return StageResult(
            name=self.name,
            status="succeeded",
            output={
                "validated_count": len(validated),
                "confirmed": confirmed,
                "refuted": refuted,
                "inconclusive": len(validated) - confirmed - refuted,
            },
        )
