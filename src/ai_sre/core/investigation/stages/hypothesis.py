"""HypothesisStage — agentic tool-loop producing ranked hypotheses (spec 0009).

The model is given the alert + service + triage context and the read-only
tools (`query_prometheus`, `list_metric_names`, `get_service_dependencies`,
`get_alert_details`). It investigates, then emits a JSON list of up to N
ranked hypotheses, each with initial confidence + evidence references.

Resilience: if no gateway is configured (keyless local run) the stage degrades
to a placeholder; if the model's final message doesn't parse into hypotheses
the stage completes `partial` so the report stage still runs.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ai_sre.core.investigation.context import (
    Hypothesis,
    InvestigationContext,
    StageResult,
)
from ai_sre.llm.prompts.hypothesis import HYPOTHESIS_PROMPT
from ai_sre.llm.prompts.system import SYSTEM_PROMPT
from ai_sre.llm.tools import REGISTRY

_MAX_HYPOTHESES = 5


def _render_user(ctx: InvestigationContext) -> str:
    alert = ctx.alert
    classification = ctx.triage.classification if ctx.triage else "unknown"
    return "\n".join(
        [
            "## Alert",
            f"name: {alert.get('alert_name')}",
            f"severity: {alert.get('severity')}",
            f"status: {alert.get('status')}",
            f"labels: {json.dumps(alert.get('labels', {}))}",
            f"annotations: {json.dumps(alert.get('annotations', {}))}",
            "",
            "## Subject service",
            f"name: {ctx.service.get('name')}",
            f"label_selector: {json.dumps(ctx.service.get('label_selector', {}))}",
            "",
            f"## Triage classification: {classification}",
            "",
            "Investigate with the available tools, then output the JSON "
            "hypothesis list described in your instructions.",
        ]
    )


def _extract_json(text: str) -> dict[str, Any] | None:
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
            return None
        try:
            obj = json.loads(match.group(0))
        except (ValueError, TypeError):
            return None
    return obj if isinstance(obj, dict) else None


def _parse_hypotheses(text: str) -> list[Hypothesis]:
    data = _extract_json(text)
    if not data:
        return []
    out: list[Hypothesis] = []
    for item in data.get("hypotheses", [])[:_MAX_HYPOTHESES]:
        if not isinstance(item, dict):
            continue
        statement = item.get("statement")
        if not statement:
            continue
        conf = item.get("initial_confidence", 0.5)
        confidence = float(conf) if isinstance(conf, (int, float)) else 0.5
        refs = [str(r) for r in item.get("evidence_refs", []) if r]
        out.append(
            Hypothesis(
                statement=str(statement), evidence_refs=refs, initial_confidence=confidence
            )
        )
    return out


class HypothesisStage:
    name = "hypothesis"
    timeout_seconds = 90
    max_tool_calls = 15

    async def execute(self, ctx: InvestigationContext) -> StageResult:
        ctx.budget.assert_within_wall()

        if ctx.gateway is None or ctx.dispatcher is None:
            ctx.hypotheses = [
                Hypothesis(
                    statement="(no LLM configured; hypothesis generation skipped)",
                    initial_confidence=0.0,
                )
            ]
            return StageResult(
                name=self.name, status="succeeded", output={"skipped": "no_gateway"}
            )

        system = f"{SYSTEM_PROMPT}\n\n{HYPOTHESIS_PROMPT}"
        resp = await ctx.gateway.tool_loop(
            system=system,
            user=_render_user(ctx),
            tools=REGISTRY.for_stage(self.name),
            dispatcher=ctx.dispatcher,
            ctx=ctx,
            budget=ctx.budget,
        )

        hypotheses = _parse_hypotheses(resp.text)
        ctx.hypotheses = hypotheses
        # No parseable hypotheses → partial, so the report stage still runs.
        status = "succeeded" if hypotheses else "partial"
        return StageResult(
            name=self.name,
            status=status,
            output={"hypothesis_count": len(hypotheses)},
        )
