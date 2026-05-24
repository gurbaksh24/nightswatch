"""Procrastinate ``App`` definition + task registrations.

This is the worker side of the queue. Application code never imports from
here — it goes through :class:`ai_sre.queue.base.JobQueue`.

Tasks are registered with ``@procrastinate_app.task(name="...")``. The task
name MUST match the value in :mod:`ai_sre.queue.procrastinate_queue`'s
``_KIND_TO_TASK_NAME``.

See LLD §15.2.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from procrastinate import App, PsycopgConnector

from ai_sre.config import get_settings
from ai_sre.connectors.registry import ConnectorRegistry
from ai_sre.core.integration.repository import IntegrationRepository
from ai_sre.db import get_sessionmaker
from ai_sre.utils.crypto import EnvelopeEncryptionService
from ai_sre.utils.logging import get_logger

logger = get_logger(__name__)


def _build_app() -> App:
    settings = get_settings()
    # Procrastinate needs a libpq-style URL; SQLAlchemy uses postgresql+asyncpg.
    conninfo = settings.db_url.replace("postgresql+asyncpg://", "postgresql://")
    return App(connector=PsycopgConnector(conninfo=conninfo))


procrastinate_app: App = _build_app()


def _build_connector_registry() -> ConnectorRegistry:
    """Construct a :class:`ConnectorRegistry` against current settings.

    Not cached — construction is cheap (no connections opened until a query
    actually runs), and skipping the cache avoids stale-sessionmaker
    hazards in tests where the DB URL is rewritten per-test.
    """
    settings = get_settings()
    return ConnectorRegistry(
        sessionmaker=get_sessionmaker(),
        crypto=EnvelopeEncryptionService(
            settings.tenant_encryption_key.get_secret_value()
        ),
        query_timeout_seconds=settings.prom_query_timeout_seconds,
        max_points=settings.prom_max_points,
        max_series=settings.prom_max_series,
    )


@procrastinate_app.task(name="run_investigation", queue="investigations", retry=3)
async def run_investigation_task(investigation_id: str) -> None:
    """Procrastinate-side entrypoint. Hands off to the orchestrator.

    The orchestrator is the source of truth for what work needs doing; this
    function only resolves dependencies and calls ``run(...)``. Stage-level
    idempotency in the orchestrator makes Procrastinate retries safe.

    Implementation tracked by spec 0007.
    """
    log = logger.bind(investigation_id=investigation_id)
    log.info("worker.run_investigation.start")
    _ = UUID(investigation_id)
    raise NotImplementedError


async def run_health_check(tenant_id: str, integration_id: str) -> None:
    """Probe one integration and write the outcome back to its row.

    Body of the ``run_health_check`` Procrastinate task, extracted so tests
    can drive it directly without going through the queue. Steps:

        1. Resolve the connector through the registry (loads + decrypts).
        2. Call ``connector.health_check()``.
        3. Update ``integration.status`` and ``last_health_check_at`` in the
           DB. Failure → ``unhealthy``; success → ``healthy``; missing
           integration → log + return (idempotent against deletions).

    Returns silently on any error. The task wrapper handles Procrastinate
    retries; this function is the unit of business work.
    """
    log = logger.bind(
        tenant_id=tenant_id,
        integration_id=integration_id,
    )
    tenant_uuid = UUID(tenant_id)
    integration_uuid = UUID(integration_id)

    registry = _build_connector_registry()
    try:
        health = await registry.health_check_for(tenant_uuid, integration_uuid)
    except Exception as exc:  # surface to logs; mark unhealthy
        log.exception("worker.health_check.error", error=str(exc))
        health = None

    new_status = "healthy" if health is not None and health.healthy else "unhealthy"
    now = datetime.now(UTC)

    sm = get_sessionmaker()
    async with sm() as session:
        repo = IntegrationRepository(session, tenant_uuid)
        row = await repo.get(integration_uuid)
        if row is None:
            log.warning("worker.health_check.integration_missing")
            return
        row.status = new_status
        row.last_health_check_at = now
        await session.commit()

    log.info(
        "worker.health_check.complete",
        status=new_status,
        latency_ms=health.latency_ms if health is not None else None,
    )


@procrastinate_app.task(name="run_health_check", queue="integrations", retry=2)
async def run_health_check_task(tenant_id: str, integration_id: str) -> None:
    """Procrastinate-side entrypoint. Thin wrapper over :func:`run_health_check`.

    Registered with ``retry=2``: if Prometheus is genuinely down we don't
    want to thrash retries; we record ``unhealthy`` and move on. The retries
    cover transient transport hiccups (DNS blip, etc).
    """
    await run_health_check(tenant_id, integration_id)
