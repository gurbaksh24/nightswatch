"""Procrastinate implementation of :class:`JobQueue`.

This is the ONLY file in ``src/`` allowed to import ``procrastinate``. The
rest of the code talks to the :class:`JobQueue` protocol.

Wiring at app startup::

    from ai_sre.workers.app import procrastinate_app
    from ai_sre.queue.procrastinate_queue import ProcrastinateJobQueue

    job_queue = ProcrastinateJobQueue(procrastinate_app)

Then ``job_queue`` is provided to anything that needs to enqueue (webhook
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


# Map JobQueue ``kind`` → Procrastinate task name. Keeping this here (not in
# workers/app.py) means callers never see Procrastinate at all.
_KIND_TO_TASK_NAME: dict[str, str] = {
    "investigation": "run_investigation",
    "health_check": "run_health_check",
    "metric_catalog_refresh": "run_metric_catalog_refresh",
    "topology_refresh": "run_topology_refresh",
    # Add new kinds here as they're introduced. The matching task is
    # registered in workers/app.py.
}


class ProcrastinateJobQueue(JobQueue):
    """Adapter over a Procrastinate ``App``."""

    def __init__(self, app: ProcrastinateApp) -> None:
        self._app = app

    async def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        delay_seconds: int = 0,
    ) -> JobId:
        """Defer a job onto the Procrastinate queue.

        Returns the procrastinate job id as a string. ``JobQueueError`` is
        raised for unknown kinds, unregistered tasks, or transport errors.
        """
        task_name = _KIND_TO_TASK_NAME.get(kind)
        if task_name is None:
            raise JobQueueError(f"Unknown job kind: {kind!r}")
        if task_name not in self._app.tasks:
            raise JobQueueError(
                f"Task {task_name!r} is not registered on the Procrastinate app."
            )
        task = self._app.tasks[task_name]

        schedule_kwargs: dict[str, Any] = {}
        if delay_seconds > 0:
            schedule_kwargs["schedule_in"] = {"seconds": delay_seconds}

        try:
            job_id = await task.defer_async(**payload, **schedule_kwargs)
        except Exception as exc:  # procrastinate raises its own ConnectorErrors
            raise JobQueueError(
                f"Failed to enqueue {kind!r}: {exc}"
            ) from exc

        logger.info("queue.enqueue", kind=kind, task=task_name, job_id=str(job_id))
        return str(job_id)

    async def cancel(self, job_id: JobId) -> None:
        """Cancel a queued (not yet running) job. No-op if already running."""
        try:
            await self._app.job_manager.cancel_job_by_id_async(int(job_id))
        except ValueError as exc:
            raise JobQueueError(f"Invalid job id {job_id!r}.") from exc
        except Exception as exc:  # procrastinate transport errors
            raise JobQueueError(f"Failed to cancel job {job_id}: {exc}") from exc
