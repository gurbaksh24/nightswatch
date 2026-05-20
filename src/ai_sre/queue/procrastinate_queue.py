"""Procrastinate implementation of `JobQueue`.

This is the ONLY file in `src/` allowed to import `procrastinate`. The rest
of the code talks to the `JobQueue` protocol.

Wiring at app startup:

    from ai_sre.workers.app import procrastinate_app
    from ai_sre.queue.procrastinate_queue import ProcrastinateJobQueue

    job_queue = ProcrastinateJobQueue(procrastinate_app)

Then `job_queue` is provided to anything that needs to enqueue (webhook
receiver, orchestrator for re-enqueue cases, scheduled-task triggers).

See LLD §15.2.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ai_sre.queue.base import JobId, JobQueue, JobQueueError
from ai_sre.utils.logging import get_logger

if TYPE_CHECKING:
    from procrastinate import App as ProcrastinateApp

logger = get_logger(__name__)


# Map kind → Procrastinate task name. Keeping this here (not in workers/app.py)
# means callers don\'t see Procrastinate at all.
_KIND_TO_TASK_NAME: dict[str, str] = {
    "investigation": "run_investigation",
    # Add new kinds here as they\'re introduced. The matching task is
    # registered in workers/app.py.
}


class ProcrastinateJobQueue(JobQueue):
    """Adapter over a Procrastinate App."""

    def __init__(self, app: ProcrastinateApp) -> None:
        self._app = app

    async def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        delay_seconds: int = 0,
    ) -> JobId:
        task_name = _KIND_TO_TASK_NAME.get(kind)
        if task_name is None:
            raise JobQueueError(f"Unknown job kind: {kind!r}")
        try:
            task = self._app.tasks[task_name]
        except KeyError as e:
            raise JobQueueError(
                f"Task {task_name!r} is not registered on the Procrastinate app"
            ) from e

        # TODO(spec-NNNN: queue):
        #   * Call task.defer_async(**payload, schedule_in={"seconds": delay_seconds}).
        #   * Coerce the returned job_id to a string and return it.
        #   * Wrap underlying procrastinate exceptions in JobQueueError.
        logger.info("queue.enqueue", kind=kind, task=task_name)
        raise NotImplementedError

    async def cancel(self, job_id: JobId) -> None:
        # TODO(spec-NNNN: queue): use procrastinate\'s job manager to cancel.
        raise NotImplementedError
