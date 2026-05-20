# `ai_sre.core.investigation`

The heart of the system. Given an alert, run a staged pipeline to produce a
structured Root Cause Analysis.

## Files

| File | Role |
|---|---|
| `orchestrator.py` | Drives a single investigation: load state, run stages, finalize, dispatch delivery. Owns top-level exception handling and partial-result logic. |
| `pipeline.py` | `Pipeline` and `Stage` protocol. Defines the default stage order. |
| `context.py` | `InvestigationContext` — the typed bag passed through stages. |
| `budget.py` | `Budget` — wall, tool-call, token, and cost limits. Raises `BudgetExhausted`. |
| `repository.py` | Tenant-scoped persistence for investigation / stage / tool_call / report rows. |
| `stages/` | One file per stage (see `stages/README.md`). |

## Architecture

```
Worker dequeues InvestigationJob
        ▼
InvestigationOrchestrator.run(investigation_id)
        │
        ├── load Investigation, Alert, Service, Topology, MetricCatalog
        ├── build InvestigationContext (immutable + mutable buckets)
        ├── for stage in Pipeline.stages:
        │       ├── persist stage(PENDING → RUNNING)
        │       ├── stage.execute(ctx)          # raises BudgetExhausted to bail
        │       └── persist stage(SUCCEEDED / FAILED / TIMED_OUT)
        ├── persist Report
        └── DeliveryDispatcher.dispatch(...)
```

## Idempotency

`orchestrator.run` is safe to retry. Each stage checks `ctx.has_completed(stage)`
before running. Crashed workers re-lease and resume from the next pending stage.

## Why staged?

See `docs/adr/0003-investigation-as-agent.md`. TL;DR: stages give us budget
gating, focused prompts, debuggable failures, and isolated prompt iteration.

## Where to make changes

- **New tool the LLM can call:** add to `ai_sre.llm.tools`, register in the
  appropriate stage's `tools_for_stage` set.
- **New investigation stage:** new file in `stages/`, register in
  `Pipeline.default()`. Each stage is a `Stage` protocol implementation.
- **Tighter budgets / different SLAs:** `budget.py` defaults + `config.py`.
- **Different prompt behaviour:** `ai_sre.llm.prompts.*`. Bump `PROMPT_VERSION`.

## Contract for a Stage

```python
class Stage(Protocol):
    name: str
    timeout_seconds: int
    max_tool_calls: int
    async def execute(self, ctx: InvestigationContext) -> StageResult: ...
```

A stage:
- Reads from `ctx` and writes its outputs back to it.
- Does NOT touch the database directly. The orchestrator persists the result.
- MAY call the LLM Gateway and connectors — both through their abstractions.
- MUST honour `ctx.budget` — call `assert_can_*` before every expensive op.
