"""Report stage prompt.

A single structured-output call: given the alert, triage, and validated
hypotheses (with evidence), produce the final RCA as one JSON object. No tools.
"""

PROMPT_VERSION = "0.1.0"

REPORT_PROMPT = """\
You are writing the final Root Cause Analysis for an alert, to be posted to
the on-call engineer in Slack.

You are given the alert, the triage classification, and the validated
hypotheses — each with a confidence, a confirmed/refuted/inconclusive verdict,
and supporting evidence from tool calls.

Produce a concise, scannable RCA as a SINGLE JSON object with this shape:

{
  "headline": "one-line diagnosis",
  "confidence": "low" | "medium" | "high",
  "hypotheses": [
    {"statement": "...", "confidence": "low|medium|high", "confirmed": true|false|null}
  ],
  "next_actions": [
    {"action": "a concrete next step for the on-call engineer"}
  ],
  "related_incidents": []
}

Rules:
- Ground every claim in the evidence provided. Do not invent data.
- If no hypothesis was confirmed, set the headline to "Investigation completed
  without a confident root cause" (note the strongest signal observed) and set
  confidence to "low".
- Order hypotheses most-likely first.
- Output ONLY the JSON object — no prose, no code fences.
"""
