# `ai_sre.workers`

Procrastinate worker entrypoint and task definitions.

## Files

- `app.py` — the `procrastinate_app` instance and all `@procrastinate_app.task`
  registrations. **Adding a new background job kind starts here.**
- `investigation_worker.py` — the process entrypoint. Calls
  `procrastinate_app.run_worker_async(...)`. Run with
  `python -m ai_sre.workers.investigation_worker`.

## Adding a new job kind

1. In `workers/app.py`, define an `async def` task with `@procrastinate_app.task(name="...", queue="...", retry=N)`.
2. In `queue/procrastinate_queue.py`, add the `kind → task_name` mapping in
   `_KIND_TO_TASK_NAME`.
3. Callers use `await job_queue.enqueue(kind, payload)` — they never touch
   Procrastinate directly.

## Rules

- Workers MUST be idempotent. Procrastinate may re-deliver a job after a
  worker crash; the orchestrator\'s stage-level idempotency (see LLD §7)
  is what keeps this safe.
- The DB row is the source of truth, not the queue message. Tasks should
  resolve work from `investigation_id` rather than carrying state in payloads.
- Workers MUST set `tenant_id` and (when applicable) `investigation_id` on
  the logger before doing any tenant work.

## Why not put orchestrator code here

The orchestrator lives in `core/investigation/` — domain logic, queue-agnostic.
The worker task in `app.py` is a thin wrapper that resolves dependencies and
calls `orchestrator.run(id)`. This keeps the orchestrator testable without
Procrastinate and lets us swap the queue (see ADR-0004) without touching it.
