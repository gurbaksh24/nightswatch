# `ai_sre.queue`

The seam between the rest of the codebase and the underlying queue
implementation (Procrastinate today; possibly something else later).

## Rule

`base.py` is the only file outside this package that *anything* may import
from. Procrastinate is imported in exactly one place: `procrastinate_queue.py`.

If a future PR adds `from procrastinate import ...` anywhere else in `src/`,
that's a review block.

## Why so strict

Procrastinate is a good choice today. The reasoning that picked it (Postgres
already there, asyncio-native, throughput more than enough) might change. The
tighter the seam, the cheaper the swap when it does. See `docs/adr/0004-job-queue.md`.

## What `JobQueue` doesn\'t expose

There is intentionally no `dequeue` method. Workers register handler
functions with Procrastinate at startup (see `workers/app.py`); Procrastinate
calls them. The application-side surface is just `enqueue` + `cancel`.

## Files

- `base.py` — `JobQueue` protocol, exceptions, job-id type alias.
- `procrastinate_queue.py` — the only Procrastinate-aware code in the repo.
