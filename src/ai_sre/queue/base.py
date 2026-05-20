"""JobQueue protocol — the only abstraction over the queue.

Workers do NOT use this interface; they register handlers with Procrastinate
directly in `workers/app.py`. Application code (webhooks, orchestrator,
maintenance schedulers) uses `JobQueue.enqueue(...)` to put work on the queue.

See LLD §15 and ADR-0004.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


JobId = str


class JobQueueError(Exception):
    """Raised when a queue operation fails."""


@runtime_checkable
class JobQueue(Protocol):
    """Application-side queue surface. Implemented by ProcrastinateJobQueue.

    `kind` maps to a registered task name (e.g. "investigation"). The
    mapping from `kind` to the actual handler is owned by the queue
    implementation; callers should not need to know.
    """

    async def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        delay_seconds: int = 0,
    ) -> JobId:
        """Schedule a job. Returns an opaque id usable with `cancel`.

        Raises:
            JobQueueError: if the queue is unreachable or the kind is unknown.
        """
        ...

    async def cancel(self, job_id: JobId) -> None:
        """Cancel a queued (not yet running) job. No-op if already running.

        Raises:
            JobQueueError: if the queue is unreachable.
        """
        ...
