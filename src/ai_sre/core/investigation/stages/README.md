# Pipeline stages

Each stage is a `Stage` (see `../pipeline.py`). Stages communicate only via
`InvestigationContext`. They MUST NOT touch the DB directly — the orchestrator
persists outcomes.

## The five MVP stages

| Stage | Type | Reads | Writes |
|---|---|---|---|
| `TriageStage` | deterministic-first, LLM only if ambiguous | alert, recent investigations | `ctx.triage` |
| `ContextAssemblyStage` | deterministic, parallel fetch | service, topology, catalog | `ctx.context` |
| `HypothesisStage` | **agentic** (LLM tool-loop) | everything above | `ctx.hypotheses` |
| `ValidationStage` | **agentic** (LLM tool-loop per hypothesis) | hypotheses | `ctx.validated` |
| `ReportStage` | structured-output LLM call | validated hypotheses + context | `ctx.report` |

## Budget discipline

Every stage MUST call `ctx.budget.assert_within_wall()` at entry and
`ctx.budget.assert_can_call_*` before any expensive op. The orchestrator
catches `BudgetExhausted` at the pipeline level — let it propagate.

## Adding a new stage

1. New file `stages/<name>.py` with a class implementing `Stage`.
2. Register in `Pipeline.default()`.
3. Decide what tools (if any) it gets via `ai_sre.llm.tools.for_stage(name)`.
4. Add unit tests with a `FakeLLMProvider`.
5. Document inputs/outputs here.

## Idempotency

If the orchestrator restarts mid-investigation, completed stages are skipped
via `ctx.has_completed(stage.name)`. A stage's `execute` MUST be safe to call
on a partial context.
