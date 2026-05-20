"""Procrastinate `App` definition + task registrations.

This is the worker side of the queue. Application code never imports from
here — it goes through `queue.base.JobQueue`.

Tasks are registered with `@procrastinate_app.task(name="...")`. The task
name MUST match the value in `queue/procrastinate_queue.py:_KIND_TO_TASK_NAME`.

See LLD §15.2.
"""

from __future__ import annotations

from uuid import UUID

from procrastinate import App, PsycopgConnector

from ai_sre.config import get_settings
from ai_sre.utils.logging import get_logger

logger = get_logger(__name__)


def _build_app() -> App:
    settings = get_settings()
    # Procrastinate needs a libpq-style URL; SQLAlchemy uses postgresql+asyncpg.
    # See https://procrastinate.readthedocs.io/ for connector options.
    conninfo = settings.db_url.replace("postgresql+asyncpg://", "postgresql://")
    return App(connector=PsycopgConnector(conninfo=conninfo))


procrastinate_app: App = _build_app()


@procrastinate_app.task(name="run_investigation", queue="investigations", retry=3)
async def run_investigation_task(investigation_id: str) -> None:
    """Procrastinate-side entrypoint. Hands off to the orchestrator.

    The orchestrator is the source of truth for what work needs doing; this
    function only resolves dependencies and calls `run(...)`. Stage-level
    idempotency in the orchestrator makes Procrastinate retries safe.

    Implementation tracked by spec-NNNN.
    """
    log = logger.bind(investigation_id=investigation_id)
    log.info("worker.run_investigation.start")
    # TODO(spec-NNNN: investigation-worker):
    #   1. Build / fetch the orchestrator from a per-process registry
    #      (created at worker startup).
    #   2. await orchestrator.run(UUID(investigation_id)).
    #   3. Let exceptions propagate — Procrastinate handles retry per the
    #      `retry=3` config above.
    _ = UUID(investigation_id)
    raise NotImplementedError
