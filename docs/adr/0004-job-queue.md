# ADR-0004: Procrastinate for the job queue, Temporal as the future escape hatch

**Status:** Accepted
**Date:** 2026-05-13

## Context

The platform needs a job queue to deliver investigation work from the webhook receiver to the worker pool. Workload characteristics:

- ~0.2 jobs/sec steady-state, ~1 job/sec burst.
- Each job runs 2–5 minutes (long-running, IO-heavy, LLM-bound).
- Up to 50 concurrent investigations system-wide.
- Durability is critical — we 202'd a webhook, the investigation MUST run.
- Lease semantics needed for worker-crash recovery.
- Per-job inspection (payload, error, retry state) is genuinely useful for ops.

## Options considered

| | **Procrastinate** | **Celery** | **RQ** | **Dramatiq** | **Custom (raw asyncpg)** |
|---|---|---|---|---|---|
| Backing store | Postgres | Redis / RabbitMQ | Redis | Redis / RabbitMQ | Postgres |
| New infra | None | Yes | Yes | Yes | None |
| asyncio-native | ✅ | ❌ | ❌ | Partial | ✅ |
| Dashboard | Basic, built-in | Flower (separate) | RQ Dashboard | dramatiq-dashboard | None |
| Maturity | Solid | Industry standard | Mature | Mature | N/A |
| Operational complexity | Lowest | Highest | Low | Low | None for us, all for us |

## Decision

**Procrastinate** for MVP and the foreseeable future. Wrapped by an internal `JobQueue` interface so the choice is reversible.

## Rationale

- **No new infrastructure.** We already have Postgres. Adding a Redis broker just for the queue is the same operational mistake as adding Kafka — paying for a stateful service we don't need.
- **asyncio-native.** Workers are async functions, sharing event loop, connection pools, and HTTP clients with the rest of the codebase. Celery and RQ are sync-first; integrating them with FastAPI means thread pools and context-switching boundaries everywhere.
- **`SELECT … FOR UPDATE SKIP LOCKED`** is the right primitive for job leasing — battle-tested, well-understood, and the same approach a hand-rolled queue would use.
- **Built-in dashboard** for pending/running/failed inspection. Less polished than enterprise queues, but sufficient for MVP ops.
- **Throughput headroom.** Procrastinate handles thousands of jobs per second on modest hardware. Our workload sits in the deep margin.

## Why not Celery

Celery is the Python default. We're rejecting the default deliberately:

- Adds Redis or RabbitMQ as a hard infrastructure dependency.
- Sync-first; async integration is a known rough edge.
- Broader and more featureful than we need — bigger configuration surface, more footguns.
- Flower (the dashboard) is a separate process to deploy and run.

The "industry standard" argument is weaker than it sounds — Celery's ubiquity is partly historical. For a new project where Postgres is already there and the workload is moderate, Procrastinate is genuinely better.

## Why not "no queue, just a sweeper"

A defensible MVP alternative: skip the queue entirely. Webhook persists `investigation` with `status='pending'`, a FastAPI background task kicks off processing, a sweeper loop polls for stale `pending` rows. ~50 lines of code, zero new dependencies.

We considered this and rejected it because:

- We lose Procrastinate's dashboard and per-job inspection — meaningful at 2am.
- Job priority, delayed scheduling, dead-letter handling all require additional code.
- The migration when we eventually need a "real" queue is bigger than the upfront cost.

That said, the design respects this option: the `JobQueue` interface is small enough (`enqueue`, `cancel`) that a sweeper-based implementation is ~50 lines if we ever want to validate that path.

## The seam: `JobQueue` interface

The orchestrator and webhook code talk to `JobQueue`, never Procrastinate directly:

```python
class JobQueue(Protocol):
    async def enqueue(self, kind: str, payload: dict, *, delay_seconds: int = 0) -> str: ...
    async def cancel(self, job_id: str) -> None: ...
```

`queue/procrastinate_queue.py` is the only place Procrastinate is imported. This keeps the choice reversible.

## Durability principle

**The investigation row is the source of truth, not the queue message.** The orchestrator can be invoked with just an `investigation_id` and reconstruct what stage to resume at from `investigation_stage` rows. The queue is delivery; the DB is truth.

This means losing a queue message is recoverable (a manual replay endpoint suffices), but corrupting the DB is not. Tests verify stage-level idempotency: replaying a completed investigation is a no-op; replaying a partially-completed one resumes from the right stage.

## What about Kafka, RabbitMQ, SQS?

- **Kafka:** wrong tool. It's a log, not a queue. Massive operational footprint. No.
- **RabbitMQ:** another stateful service for no scale benefit at MVP. No.
- **SQS:** managed, so operationally simpler in production, but AWS lock-in and worse local dev. Worth reconsidering only if we commit fully to AWS for everything else.

## The future escape hatch: Temporal

The investigation orchestrator is, structurally, a workflow:

- Long-running computation (2–5 minutes)
- Multiple sequential stages
- Each stage needs durable state, retry, timeout
- Resumable after worker crashes
- Replay/backtest is a first-class need
- Per-step versioning (prompt versions)

This is the textbook Temporal workflow shape. A meaningful fraction of the code in the LLD — stage persistence, idempotency keys, partial-result handling — reinvents what Temporal gives you.

We're **not** starting on Temporal because:

- Operationally heavyweight (cluster: history service, matching service, frontend, UI, Cassandra or Postgres).
- Real learning curve.
- The "reinvented" code is a few hundred lines, easy to maintain at MVP scale.

But when (not if, probably) the orchestrator code starts feeling like a fighting-against-the-grain workflow engine — likely around month 6–9 if the product takes off — **the move is Temporal, not "scale Procrastinate harder."** Procrastinate stays as it is. Temporal replaces the orchestrator. Stages become Temporal activities; the existing connector/LLM/delivery layers are untouched.

This is recorded here so the team doesn't reach for "let's switch to Kafka" or "let's write a custom workflow engine" when the symptoms appear.

## Consequences

- One more Postgres migration set to manage (Procrastinate's, in addition to ours). Mitigation: Procrastinate's CLI runs in the deploy pre-start hook.
- Procrastinate version upgrades will occasionally require schema migrations of its own. Tracked in the deploy runbook.
- The `JobQueue` interface must stay tight. Any leak of Procrastinate-isms into orchestrator or stage code is a review-block.

## Revisiting

We revisit this ADR when any of the following is true:

- Procrastinate is failing to keep up with throughput (would be unprecedented at our scale).
- The orchestrator code is hitting structural pain that's better solved by Temporal (the more likely trigger).
- We commit to a specific cloud and a managed queue (SQS, Pub/Sub) becomes operationally preferable.
