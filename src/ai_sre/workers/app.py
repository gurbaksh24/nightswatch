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
from ai_sre.core.alert.repository import AlertRepository
from ai_sre.core.integration.repository import IntegrationRepository
from ai_sre.core.investigation.orchestrator import InvestigationOrchestrator
from ai_sre.core.investigation.pipeline import Pipeline
from ai_sre.core.investigation.repository import InvestigationRepository
from ai_sre.core.investigation.stages.context_assembly import ContextAssemblyStage
from ai_sre.core.investigation.stages.hypothesis import HypothesisStage
from ai_sre.core.investigation.stages.report import ReportStage
from ai_sre.core.investigation.stages.triage import TriageStage
from ai_sre.core.investigation.stages.validation import ValidationStage
from ai_sre.core.service.catalog_service import CatalogService
from ai_sre.core.service.repository import (
    MetricCatalogRepository,
    ServiceDependencyRepository,
    ServiceRepository,
)
from ai_sre.core.service.topology_service import TopologyService
from ai_sre.core.tenant.repository import TenantRepository
from ai_sre.db import get_sessionmaker
from ai_sre.models.investigation import Investigation
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


def _build_investigation_pipeline(
    repo: InvestigationRepository, alert_repo: AlertRepository
) -> Pipeline:
    """Compose the MVP pipeline. TriageStage gets DB access (known-issue +
    noise lookups); the remaining stages are deterministic stubs until their
    own specs (0009/0011/0012)."""
    return Pipeline(
        stages=(
            TriageStage(repo, alert_repo),
            ContextAssemblyStage(),
            HypothesisStage(),
            ValidationStage(),
            ReportStage(),
        )
    )


async def run_investigation(investigation_id: str) -> None:
    """Run one investigation through the orchestrator.

    Extracted from the task wrapper so tests can drive it directly. Resolves
    the tenant from the investigation id (the queue message carries only the
    id) via an unscoped bootstrap load, then builds tenant-scoped repos and
    runs the pipeline. The orchestrator's stage-level idempotency makes
    Procrastinate retries safe.
    """
    log = logger.bind(investigation_id=investigation_id)
    inv_uuid = UUID(investigation_id)

    sm = get_sessionmaker()
    async with sm() as session:
        # Bootstrap: discover the tenant. Unscoped by necessity — same role as
        # the auth find_by_hash lookup. Everything after is tenant-scoped.
        inv = await session.get(Investigation, inv_uuid)
        if inv is None:
            log.warning("worker.run_investigation.not_found")
            return
        repo = InvestigationRepository(session, inv.tenant_id)
        alert_repo = AlertRepository(session, inv.tenant_id)
        orchestrator = InvestigationOrchestrator(
            _build_investigation_pipeline(repo, alert_repo), repo
        )
        await orchestrator.run(inv_uuid)
        await session.commit()

    log.info("worker.run_investigation.complete")


@procrastinate_app.task(name="run_investigation", queue="investigations", retry=3)
async def run_investigation_task(investigation_id: str) -> None:
    """Procrastinate-side entrypoint. Thin wrapper over :func:`run_investigation`."""
    await run_investigation(investigation_id)


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


# ---- Discovery (spec 0005): metric catalog + topology refresh ----


async def run_metric_catalog_refresh(tenant_id: str, service_id: str) -> None:
    """Refresh the metric catalog for one service.

    Extracted from the task wrapper so tests can drive it directly. Loads
    the service, discovers metrics via the connector, and replaces the
    catalog rows. Idempotent: a missing service (deleted between enqueue and
    pickup) logs and returns cleanly.
    """
    log = logger.bind(tenant_id=tenant_id, service_id=service_id)
    tenant_uuid = UUID(tenant_id)
    service_uuid = UUID(service_id)

    registry = _build_connector_registry()
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            catalog_service = CatalogService(
                ServiceRepository(session, tenant_uuid),
                MetricCatalogRepository(session, tenant_uuid),
                registry,
            )
            result = await catalog_service.refresh_by_id(service_uuid)
            await session.commit()
    finally:
        await registry.close_all()

    if result is None:
        log.warning("worker.metric_catalog_refresh.service_missing")
        return
    log.info(
        "worker.metric_catalog_refresh.complete",
        discovered=result.discovered,
        status=result.status,
    )


async def run_topology_refresh(tenant_id: str, service_id: str) -> None:
    """Refresh the dependency topology for one service. See
    :func:`run_metric_catalog_refresh` for the shape/idempotency notes."""
    log = logger.bind(tenant_id=tenant_id, service_id=service_id)
    tenant_uuid = UUID(tenant_id)
    service_uuid = UUID(service_id)

    registry = _build_connector_registry()
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            topology_service = TopologyService(
                ServiceRepository(session, tenant_uuid),
                ServiceDependencyRepository(session, tenant_uuid),
                registry,
            )
            result = await topology_service.refresh_by_id(service_uuid)
            await session.commit()
    finally:
        await registry.close_all()

    if result is None:
        log.warning("worker.topology_refresh.service_missing")
        return
    log.info(
        "worker.topology_refresh.complete",
        discovered=result.discovered,
        status=result.status,
    )


async def list_services_for_refresh() -> list[tuple[str, str]]:
    """Return ``(tenant_id, service_id)`` for every registered service.

    Used by the periodic refresh to fan out. Tenant enumeration goes through
    the root :class:`TenantRepository`; each service lookup is tenant-scoped.
    """
    sm = get_sessionmaker()
    pairs: list[tuple[str, str]] = []
    async with sm() as session:
        tenants = await TenantRepository(session).list_all()
        for tenant in tenants:
            service = await ServiceRepository(session, tenant.id).get_for_tenant()
            if service is not None:
                pairs.append((str(tenant.id), str(service.id)))
    return pairs


async def _enqueue_all_discovery_refreshes() -> int:
    """Defer a catalog + topology refresh for every service. Returns the
    number of services fanned out to."""
    pairs = await list_services_for_refresh()
    for tenant_id, service_id in pairs:
        await run_metric_catalog_refresh_task.defer_async(
            tenant_id=tenant_id, service_id=service_id
        )
        await run_topology_refresh_task.defer_async(
            tenant_id=tenant_id, service_id=service_id
        )
    return len(pairs)


@procrastinate_app.task(name="run_metric_catalog_refresh", queue="discovery", retry=2)
async def run_metric_catalog_refresh_task(tenant_id: str, service_id: str) -> None:
    """Procrastinate entrypoint for a single metric-catalog refresh."""
    await run_metric_catalog_refresh(tenant_id, service_id)


@procrastinate_app.task(name="run_topology_refresh", queue="discovery", retry=2)
async def run_topology_refresh_task(tenant_id: str, service_id: str) -> None:
    """Procrastinate entrypoint for a single topology refresh."""
    await run_topology_refresh(tenant_id, service_id)


@procrastinate_app.periodic(cron="0 */6 * * *")
@procrastinate_app.task(name="refresh_all_discovery", queue="discovery", pass_context=False)
async def refresh_all_discovery_task(timestamp: int) -> None:
    """Every 6h: fan out a catalog + topology refresh for every service
    (FR-3.2). The ``timestamp`` arg is Procrastinate's periodic contract."""
    count = await _enqueue_all_discovery_refreshes()
    logger.info("worker.refresh_all_discovery.enqueued", services=count)
