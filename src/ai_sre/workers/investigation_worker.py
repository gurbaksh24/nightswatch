"""Investigation worker entrypoint.

Run as `python -m ai_sre.workers.investigation_worker`. In production,
multiple replicas can run in parallel — Procrastinate handles leasing via
`SELECT ... FOR UPDATE SKIP LOCKED` against the queue table.

The actual work is in `workers/app.py` (task definitions). This module is
just the process entrypoint.
"""

from __future__ import annotations

import asyncio

from ai_sre.config import get_settings
from ai_sre.utils.logging import configure_logging, get_logger
from ai_sre.workers.app import procrastinate_app

logger = get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    logger.info(
        "worker.start",
        queues=["investigations"],
        concurrency=settings.queue_concurrency,
    )
    async with procrastinate_app.open_async():
        await procrastinate_app.run_worker_async(
            queues=["investigations"],
            concurrency=settings.queue_concurrency,
        )


if __name__ == "__main__":
    asyncio.run(main())
