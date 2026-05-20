"""Validation stage prompt.

Run once per top-K hypothesis. The model is asked to design a confirming or
refuting test, run it, and emit a confidence update with reasoning.
"""

PROMPT_VERSION = "0.1.0"

VALIDATION_PROMPT = """\
You are validating one specific hypothesis about an alert.

Hypothesis: {hypothesis_statement}
Initial confidence: {initial_confidence}

Design ONE query (or a small set) that would meaningfully confirm or refute
this hypothesis. Run it with the tools. Then output:

```json
{{
  "confidence": 0.0-1.0,
  "reasoning": "1-3 sentences explaining what the data showed",
  "verdict": "confirmed" | "refuted" | "inconclusive"
}}
```

Do NOT propose new hypotheses here — only evaluate this one.
"""
