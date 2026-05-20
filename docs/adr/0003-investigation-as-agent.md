# ADR-0003: Staged pipeline with agentic stages, not a free-roaming agent

**Status:** Accepted
**Date:** 2026-05-13

## Context

Two extreme designs for the investigation engine:

1. **Free-roaming agent:** give the LLM all the tools and let it decide everything — when to stop, which tools to call, how to structure output.
2. **Rigid hand-coded pipeline:** every step is a hand-coded sequence of queries; the LLM is only used to summarise.

## Decision

**Hybrid: a fixed pipeline of named stages; some stages are agentic (LLM with tools), others are deterministic.**

```
Triage → Context Assembly → Hypothesis (agentic) → Validation (agentic) → Report (structured)
```

## Rationale

- A free-roaming agent is unpredictable in **cost** and **latency**. Both are critical.
- A free-roaming agent is hard to **debug** when wrong. With stages, we can pinpoint which stage failed.
- A free-roaming agent makes **prompt iteration** dangerous: changing the system prompt risks regressing every behaviour at once. With stages, each prompt is small and focused.
- A rigid pipeline gives up the actual value: LLMs are good at hypothesis generation and tool selection — exactly the messy middle of an investigation. We use them there.
- Deterministic stages (Triage, Context Assembly, Report assembly) are predictable and testable.

## Consequences

- Pipeline boundaries are part of the system architecture, not "implementation details."
- Adding a new capability often means a new stage rather than mutating an existing prompt.
- Each stage's input and output is a typed contract; this is enforced.
- We forfeit some emergent behaviour that a free-roaming agent might find. We're betting that the structure is worth it for production reliability.

## Revisiting

If, after 6 months of operation, our biggest quality issue is "the agent doesn't explore enough," we'll consider an open-exploration stage that runs before Validation. We won't replace the whole pipeline.
