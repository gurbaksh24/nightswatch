# Spec 0015: `search_runbooks` + `search_past_incidents` tools

> Wire the knowledge service into the LLM toolbox. Also: ingest completed investigations as future past_incidents.

**Spec ID:** 0015
**Status:** ready-for-agent
**Depends on:** 0008, 0009, 0012, 0014

---

## Motivation

FR-6.5, FR-6.6, FR-7.3.

## Scope

- [ ] `search_runbooks(query, k=5)` tool — vector search over `kind="runbook"` docs.
- [ ] `search_past_incidents(query, k=5)` tool — vector search over `kind="past_investigation"` docs.
- [ ] Both tools registered for HypothesisStage and ValidationStage.
- [ ] Orchestrator: at the end of `run()`, call `KnowledgeService.ingest_past_investigation(investigation, report)` for `succeeded` and `partial` investigations.
- [ ] Tests.

## Out of scope

- Hybrid (vector + keyword) search.
- Reranking.

## Context

- Specs 0008, 0009, 0014.

## Design

### Files to touch

- `src/ai_sre/llm/tools/search_runbooks.py` — new.
- `src/ai_sre/llm/tools/search_past_incidents.py` — new.
- `src/ai_sre/core/knowledge/service.py` — add `ingest_past_investigation`.
- `src/ai_sre/core/investigation/orchestrator.py` — call ingest at end of `run()`.
- Tests.

### Edge cases

- No knowledge_docs of the relevant kind → tool returns empty list, model handles gracefully.
- Embedding fails mid-ingest → don't roll back the investigation; log + flag for re-ingest.

## Tests

- Unit: tool handlers return ranked chunks.
- Integration: run an investigation → past_investigation doc exists → next investigation can find it via `search_past_incidents`.

## Definition of done

- [ ] Scope complete.
- [ ] On a second similar alert, `search_past_incidents` returns the first investigation as top result.
