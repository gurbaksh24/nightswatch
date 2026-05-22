# Spec 0007: Investigation orchestrator skeleton + Triage stage

> The state machine that runs an investigation, with all five stages wired in but Hypothesis / Validation / Report as deterministic stubs. End-to-end an alert produces an investigation row with all stage rows present.

**Spec ID:** 0007
**Status:** ready-for-agent
**Depends on:** 0001, 0002, 0004, 0005, 0006

---

## Motivation

FR-5.1–5.3: the staged pipeline. This spec lands the engine without any LLM dependency — proves the plumbing.

## Scope

- [ ] `Pipeline` and `Stage` Protocol in `core/investigation/pipeline.py`.
- [ ] `InvestigationContext` dataclass with stage-output slots and `tool_calls` audit trail.
- [ ] `Budget` dataclass with `BudgetExhausted`.
- [ ] `InvestigationOrchestrator.run(investigation_id)`:
  - Idempotent: skips already-completed stages by reading `investigation_stage` rows.
  - Per-stage timeout enforcement (`asyncio.wait_for`).
  - Catches stage exceptions and continues (except `BudgetExhausted` which short-circuits).
  - Persists `investigation_stage` row at end of each stage.
- [ ] Real `TriageStage` — deterministic-only: dedupe-check, known-issue lookup (search past investigations with same fingerprint in last 24h), noise heuristic (more than N flaps in M minutes).
- [ ] Stub stages: `ContextAssemblyStage` returns empty context, `HypothesisStage` returns one fixed hypothesis ("placeholder"), `ValidationStage` is a no-op, `ReportStage` builds a minimal report from whatever's in context.
- [ ] Wire the orchestrator to be invoked by `run_investigation_task` in `workers/app.py`.
- [ ] Tests with a `FakeJobQueue` and Postgres.

## Out of scope

- Real Hypothesis / Validation / Report (specs 0009, 0011, 0012).
- LLM Gateway (spec 0008).
- Delivery (spec 0010).

## Context

- `docs/03-lld.md` §7, §8, §9
- `src/ai_sre/core/investigation/*` (existing stubs)
- `src/ai_sre/workers/app.py`
- `src/ai_sre/queue/*`

## Design

### Files to touch

- `src/ai_sre/core/investigation/orchestrator.py` — implement.
- `src/ai_sre/core/investigation/pipeline.py` — implement.
- `src/ai_sre/core/investigation/context.py` — implement.
- `src/ai_sre/core/investigation/budget.py` — implement.
- `src/ai_sre/core/investigation/stages/triage.py` — implement.
- `src/ai_sre/core/investigation/stages/{context_assembly,hypothesis,validation,report}.py` — stub implementations.
- `src/ai_sre/core/investigation/repository.py` — new (for stage persistence + idempotent lookup).
- `src/ai_sre/workers/app.py` — replace the NotImplementedError in `run_investigation_task` with a real orchestrator call.
- Tests.

### New / changed contracts

```python
# core/investigation/pipeline.py
class Stage(Protocol):
    name: str
    timeout_seconds: int

    async def execute(self, ctx: InvestigationContext) -> StageResult: ...

@dataclass(frozen=True)
class StageResult:
    name: str
    output: dict
    error: str | None = None

# core/investigation/orchestrator.py
class InvestigationOrchestrator:
    def __init__(
        self,
        pipeline: Pipeline,
        repo: InvestigationRepository,
        # delivery: DeliveryDispatcher,   # added in spec 0010
    ) -> None: ...

    async def run(self, investigation_id: UUID) -> None: ...
```

### Edge cases

- Investigation already in `succeeded`/`partial`/`failed` → return early (idempotent).
- Stage raises `BudgetExhausted` → finalize as `partial`; mark remaining stages as `budget_exhausted`.
- Stage times out → persist `status=timed_out`; continue to next stage.
- Stage raises any other exception → persist `status=failed` with serialised error; continue.
- Worker process dies mid-stage → next worker picks up, sees no completed stage row for the current stage, retries it.

## Tests

- Unit: pipeline ordering; stage skip on resume; budget propagation; timeout finalisation.
- Unit: TriageStage classifies noise / known-issue / new correctly.
- Integration: enqueue an investigation, wait for completion, assert all 5 stage rows + 1 investigation in `succeeded`.

## Rollout

- Migration: maybe index on `investigation_stage(investigation_id, name)` if not present.
- Observability: span per stage (OTel), `aisre_investigation_duration_seconds{stage,outcome}`, `aisre_investigation_status_total{status}`.

## Definition of done

- [ ] Scope complete.
- [ ] An alert → webhook → investigation runs end-to-end and produces a "placeholder" report row in <5 seconds.
- [ ] Killing the worker mid-stage and restarting resumes correctly.

## Follow-ups

- Per-stage attempts beyond 1 (currently retries restart the stage from scratch).
- Stage-level concurrency for ContextAssembly (`asyncio.gather` fan-out).
