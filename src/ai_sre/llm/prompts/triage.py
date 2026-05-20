"""Triage stage prompt.

Used only when deterministic checks are ambiguous — most alerts skip the LLM
at this stage.
"""

PROMPT_VERSION = "0.1.0"

TRIAGE_PROMPT = """\
Classify the alert below as one of:

- `noise`        — flapping / known transient. Do NOT investigate further.
- `known_issue`  — a similar alert in the recent past was investigated. Refer
                   to its conclusion instead of repeating the work.
- `novel`        — new alert that warrants a full investigation.

You will be given the alert details and a list of similar recent alerts.
Respond with valid JSON: `{"classification": "...", "reasoning": "..."}`.
"""
