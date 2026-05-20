"""Hypothesis stage prompt.

Used in an agentic tool-loop. The model is given:
    * The alert.
    * The context summary (recent deploys, dependency health, error rate).
    * The metric catalog (browsable via tool).
    * Past incidents (searchable via tool).

It should propose up to 5 ranked hypotheses, each with at least one piece of
initial evidence backed by a tool call.
"""

PROMPT_VERSION = "0.1.0"

HYPOTHESIS_PROMPT = """\
You are investigating the cause of the alert below.

Your goal: produce up to 5 ranked hypotheses for what is causing this alert.
For each hypothesis, gather at least one piece of supporting evidence using
the available tools.

How to proceed:

1. Read the alert and context carefully.
2. Decide what queries would distinguish between plausible causes.
3. Use the `query_prometheus` tool (or others) to pull the data.
4. Refine your hypotheses based on what you find.
5. Stop when you have a strong ranked list — typically after 3-8 tool calls.

Output format (final message):

```json
{
  "hypotheses": [
    {
      "statement": "...",
      "initial_confidence": 0.0-1.0,
      "evidence_refs": ["tool_call_id or short cite"]
    },
    ...
  ]
}
```

Be honest about confidence. If two hypotheses are equally plausible, score
them equally — don't artificially rank.
"""
