# Spec 0012: ReportStage + investigation read endpoints

> Final RCA, structured-output binding, and the dashboard's read APIs.

**Spec ID:** 0012
**Status:** ready-for-agent
**Depends on:** 0008, 0011

---

## Motivation

FR-5.5 (structured output), FR-10.1 / 10.2 (list + detail).

## Scope

- [ ] `report.py` prompt; structured-output schema bound via Anthropic's tool-use-for-structured-output trick (or a single forced tool call returning the schema).
- [ ] Real `ReportStage` replacing the spec-0007 stub.
- [ ] Persist `report` row.
- [ ] Implement: `GET /v1/investigations`, `GET /v1/investigations/{id}`, `GET /v1/investigations/{id}/report`, `GET /v1/investigations/{id}/trace`.
- [ ] Tests.

## Out of scope

- Replay / backtest (spec 0016).
- Feedback endpoints (spec 0013).

## Context

- `docs/03-lld.md` §7 (orchestrator finalize), §8 (Report stage)
- `docs/04-data-model.md` — `report`
- `docs/05-api-spec.md` §Investigations

## Design

### Files to touch

- `src/ai_sre/llm/prompts/report.py` — new.
- `src/ai_sre/core/investigation/stages/report.py` — replace stub.
- `src/ai_sre/core/investigation/repository.py` — extend with read methods (paginated list with filters).
- `src/ai_sre/api/investigations.py` — implement.
- `src/ai_sre/schemas/investigation.py` — finalise (the response shapes).
- Tests.

### New / changed contracts

```python
# Pydantic schema for the LLM's structured output
class ReportOutput(BaseModel):
    headline: str
    confidence: Literal["low", "medium", "high"]
    hypotheses: list[ReportHypothesis]
    next_actions: list[NextAction]
    related_incidents: list[RelatedIncidentRef]
```

### Edge cases

- All hypotheses inconclusive → confidence="low"; headline says so plainly.
- Validation stage produced 0 confirmed hypotheses → headline is "Investigation completed without a confident root cause" + observed signals.
- LLM emits malformed structured output → re-prompt once; if still malformed, fall back to a plain-text headline + raw hypotheses; report row has `schema_version="v1-fallback"`.

## Tests

- Unit: schema binding round-trip with FakeLLMProvider.
- Unit: read filters (status, time range, confidence).
- Integration: end-to-end run → GET list contains it; GET detail returns report.

## Rollout

- Migration: none (table exists).
- Observability: `aisre_report_confidence_total{confidence}`.

## Definition of done

- [ ] Scope complete.
- [ ] End-to-end alert produces a report visible via the read APIs.
- [ ] Trace endpoint returns all stages + tool calls + LLM responses for one investigation.
