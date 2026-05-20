"""Seed the local dev database with a tenant + API key + sample integration.

Run as: `python -m scripts.dev_seed`

Idempotent — safe to run multiple times.
"""

from __future__ import annotations

import asyncio

from ai_sre.utils.logging import get_logger

logger = get_logger(__name__)


async def main() -> None:
    logger.info("seed.start")
    # TODO(spec-NNNN: dev-seed):
    #   1. Open a session_scope.
    #   2. Find-or-create a "demo" tenant.
    #   3. Issue a deterministic dev API key (printed to stdout).
    #   4. Find-or-create a Prometheus integration pointing at localhost.
    #   5. Find-or-create a "checkout" service with label_selector.
    raise NotImplementedError


if __name__ == "__main__":
    asyncio.run(main())
