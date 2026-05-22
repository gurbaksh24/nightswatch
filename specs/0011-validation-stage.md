# Spec 0011: ValidationStage

> Take the top-K hypotheses from Hypothesis stage and validate each via a targeted tool loop. Updates confidence.

**Spec ID:** 0011
**Status:** ready-for-agent
**Depends on:** 0008, 0009

---

## Motivation

FR-5.4: every claim in the final RCA must be backed by evidence. Validation is the stage that produces that evidence per hypothesis.

## Scope

- [ ] `validation.py` prompt with `PROMPT_VERSION`.
- [ ] Real `ValidationStage` replacing the spec-0007 stub.
- [ ] For each of the top-K hypotheses (default 3): tool loop asking the model "what query would confirm/refute this?" → run it → update hypothesis confidence + evidence.
- [ ] Per-hypothesis sub-budget so one bad hypothesis can't burn the whole stage budget.
- [ ] Output `validated: list[ValidatedHypothesis]` in context.
- [ ] Tests with FakeLLMProvider.

## Out of scope

- Cross-hypothesis interaction ("if A and B both seem true, prefer A").
- Active probing (running queries the LLM didn't ask for).

## Context

- `docs/03-lld.md` §8.2 (Validation stage description)
- Spec 0009 (Hypothesis output shape, tools).

## Design

### Files to touch

- `src/ai_sre/llm/prompts/validation.py` — implement.
- `src/ai_sre/core/investigation/stages/validation.py` — replace stub.
- `src/ai_sre/core/investigation/context.py` — add `ValidatedHypothesis` dataclass if not present.
- Tests.

### New / changed contracts

```python
@dataclass(frozen=True)
class ValidatedHypothesis:
    hypothesis_id: str
    statement: str
    confidence: str         # "low" / "medium" / "high"
    confirmed: bool | None  # True/False/None (couldn't determine)
    evidence: list[Evidence]  # tool call refs + textual rationale
    reasoning: str
```

### Edge cases

- Hypothesis is too vague to validate → model returns `confirmed=None` with a "needs more context" reasoning.
- All hypotheses rejected → context.validated is non-empty (the rejections themselves are useful evidence) but nothing has `confirmed=True`. Report stage handles "no confident cause."
- Sub-budget exhausted on hypothesis 1 → remaining hypotheses are left at their Hypothesis-stage confidence with a `validated=False` flag.

## Tests

- Unit: validation loop happy path; per-hypothesis sub-budget; rejection path.
- Integration: post-hypothesis context produces a populated `validated` list.

## Rollout

- Migration: none.
- Observability: `aisre_validation_hypotheses_total{outcome}` where outcome ∈ confirmed/refuted/inconclusive.

## Definition of done

- [ ] Scope complete.
- [ ] With a scripted FakeLLMProvider, three hypotheses each get a `ValidatedHypothesis` with at least one tool-call citation.
