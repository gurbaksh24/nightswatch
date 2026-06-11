"""FastAPI dependencies.

The only place where authentication and tenant resolution happen for the API.
Routes declare `tenant: TenantContext = Depends(current_tenant)` to require
auth; admin-only routes use `Depends(admin_only)`.

`TenantContext` itself lives in `ai_sre.core.tenant.context` so that core
services can return it without importing from `api/`. It is re-exported from
this module for backward-compatible route signatures.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sre.config import get_settings
from ai_sre.connectors.registry import ConnectorRegistry
from ai_sre.core.alert.repository import AlertRepository
from ai_sre.core.alert.service import AlertService
from ai_sre.core.feedback.repository import FeedbackRepository
from ai_sre.core.feedback.service import FeedbackService
from ai_sre.core.integration.repository import IntegrationRepository
from ai_sre.core.integration.service import IntegrationService
from ai_sre.core.investigation.backtest import BacktestService
from ai_sre.core.investigation.repository import (
    InvestigationRepository,
    ReportRepository,
)
from ai_sre.core.knowledge.embedding import (
    EmbeddingProvider,
    HashingEmbedder,
    build_embedder,
)
from ai_sre.core.knowledge.repository import KnowledgeRepository
from ai_sre.core.knowledge.service import KnowledgeService
from ai_sre.core.service.catalog_service import CatalogService
from ai_sre.core.service.repository import (
    MetricCatalogRepository,
    ServiceDependencyRepository,
    ServiceRepository,
)
from ai_sre.core.service.service import ServiceService
from ai_sre.core.service.topology_service import TopologyService
from ai_sre.core.tenant.api_key_service import ApiKeyService
from ai_sre.core.tenant.context import TenantContext
from ai_sre.core.tenant.repository import ApiKeyRepository, TenantRepository
from ai_sre.core.tenant.service import TenantService
from ai_sre.db import get_sessionmaker
from ai_sre.queue.base import JobQueue
from ai_sre.queue.procrastinate_queue import ProcrastinateJobQueue
from ai_sre.utils.crypto import EnvelopeEncryptionService
from ai_sre.utils.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "TenantContext",
    "admin_only",
    "current_tenant",
    "get_alert_service",
    "get_api_key_service",
    "get_backtest_service",
    "get_catalog_service",
    "get_connector_registry",
    "get_embedder",
    "get_feedback_service",
    "get_integration_service",
    "get_integration_service_for_tenant",
    "get_investigation_repo",
    "get_job_queue",
    "get_knowledge_service",
    "get_report_repo",
    "get_service_service",
    "get_session",
    "get_tenant_service",
    "get_topology_service",
]


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield an async DB session, committing on success and rolling back on error."""
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_tenant_service(
    session: AsyncSession = Depends(get_session),
) -> TenantService:
    """FastAPI dependency that constructs a `TenantService` per request."""
    return TenantService(TenantRepository(session))


def get_api_key_service(
    session: AsyncSession = Depends(get_session),
) -> ApiKeyService:
    """FastAPI dependency that constructs an `ApiKeyService` per request."""
    return ApiKeyService(ApiKeyRepository(session), TenantRepository(session))


@lru_cache(maxsize=1)
def _get_envelope_crypto() -> EnvelopeEncryptionService:
    """Process-wide envelope encryption service.

    The crypto service holds the master key — same key for every request,
    so we cache it. ``get_settings`` is also cached, which makes this safe
    against env-var changes only after a process restart (intended).
    """
    settings = get_settings()
    return EnvelopeEncryptionService(
        settings.tenant_encryption_key.get_secret_value()
    )


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "auth.invalid_token", "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def current_tenant(
    authorization: str = Header(..., alias="Authorization"),
    api_key_service: ApiKeyService = Depends(get_api_key_service),
) -> TenantContext:
    """Parse a Bearer token, verify the key, and return the tenant context."""
    if not authorization.lower().startswith("bearer "):
        raise _unauthorized("Missing bearer token.")
    token = authorization[len("Bearer ") :].strip()
    if not token:
        raise _unauthorized("Empty bearer token.")
    ctx = await api_key_service.verify(token)
    if ctx is None:
        raise _unauthorized("Invalid or revoked API key.")
    return ctx


async def admin_only(
    authorization: str = Header(..., alias="Authorization"),
) -> None:
    """Authorize admin-only endpoints (e.g. tenant creation).

    Uses a shared secret from settings. Suitable for MVP; replace with a
    real admin login when the dashboard supports it.
    """
    settings = get_settings()
    expected = f"Bearer {settings.admin_token.get_secret_value()}"
    if authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "auth.invalid_token", "message": "Admin token required."},
        )


def get_integration_service(
    tenant: TenantContext = Depends(current_tenant),
    session: AsyncSession = Depends(get_session),
) -> IntegrationService:
    """FastAPI dependency that constructs an :class:`IntegrationService`.

    The service is tenant-scoped via the repository; the crypto service is
    shared process-wide.
    """
    repo = IntegrationRepository(session, tenant.tenant_id)
    return IntegrationService(repo, _get_envelope_crypto())


@lru_cache(maxsize=1)
def _get_job_queue_impl() -> JobQueue:
    """Process-wide :class:`JobQueue`.

    Built once per process so we don't re-wrap the Procrastinate app on
    every request. Tests can override this dep via FastAPI's
    ``dependency_overrides`` to inject an in-memory queue.
    """
    # Local import keeps the api/ → workers/ direction limited to startup
    # wiring. workers/app.py is the only module allowed to import procrastinate.
    from ai_sre.workers.app import procrastinate_app

    return ProcrastinateJobQueue(procrastinate_app)


def get_job_queue() -> JobQueue:
    """FastAPI dependency that returns the process-wide job queue."""
    return _get_job_queue_impl()


def get_connector_registry() -> ConnectorRegistry:
    """FastAPI dependency that returns a :class:`ConnectorRegistry`.

    Built per-request rather than cached: construction is cheap (no
    network/DB until a query actually runs) and skipping the cache avoids
    stale-sessionmaker hazards across tests that rewrite the DB URL. Same
    reasoning as :func:`ai_sre.workers.app._build_connector_registry`.
    """
    settings = get_settings()
    return ConnectorRegistry(
        sessionmaker=get_sessionmaker(),
        crypto=_get_envelope_crypto(),
        query_timeout_seconds=settings.prom_query_timeout_seconds,
        max_points=settings.prom_max_points,
        max_series=settings.prom_max_series,
    )


def get_service_service(
    tenant: TenantContext = Depends(current_tenant),
    session: AsyncSession = Depends(get_session),
    connector_registry: ConnectorRegistry = Depends(get_connector_registry),
) -> ServiceService:
    """FastAPI dependency that constructs a :class:`ServiceService`.

    The repository is tenant-scoped; the connector registry is shared
    across requests within the process.
    """
    repo = ServiceRepository(session, tenant.tenant_id)
    return ServiceService(repo, connector_registry)


def get_catalog_service(
    tenant: TenantContext = Depends(current_tenant),
    session: AsyncSession = Depends(get_session),
    connector_registry: ConnectorRegistry = Depends(get_connector_registry),
) -> CatalogService:
    """FastAPI dependency that constructs a :class:`CatalogService`."""
    return CatalogService(
        ServiceRepository(session, tenant.tenant_id),
        MetricCatalogRepository(session, tenant.tenant_id),
        connector_registry,
    )


def get_topology_service(
    tenant: TenantContext = Depends(current_tenant),
    session: AsyncSession = Depends(get_session),
    connector_registry: ConnectorRegistry = Depends(get_connector_registry),
) -> TopologyService:
    """FastAPI dependency that constructs a :class:`TopologyService`."""
    return TopologyService(
        ServiceRepository(session, tenant.tenant_id),
        ServiceDependencyRepository(session, tenant.tenant_id),
        connector_registry,
    )


# ---- Webhook (path-scoped, no bearer) ----
#
# The Alertmanager webhook is authenticated by URL tenant_id + HMAC, not a
# bearer token, so these deps take ``tenant_id`` straight from the path
# rather than from ``current_tenant``.


def get_integration_service_for_tenant(
    tenant_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> IntegrationService:
    """IntegrationService scoped to a path ``tenant_id`` (no bearer auth).

    Used by the webhook receiver to load + decrypt the tenant's webhook
    signing secret.
    """
    return IntegrationService(
        IntegrationRepository(session, tenant_id), _get_envelope_crypto()
    )


def get_alert_service(
    tenant_id: UUID,
    session: AsyncSession = Depends(get_session),
    job_queue: JobQueue = Depends(get_job_queue),
) -> AlertService:
    """AlertService scoped to a path ``tenant_id`` (no bearer auth)."""
    settings = get_settings()
    return AlertService(
        AlertRepository(session, tenant_id),
        InvestigationRepository(session, tenant_id),
        ServiceRepository(session, tenant_id),
        job_queue,
        dedupe_window_seconds=settings.inv_dedupe_window_seconds,
    )


def get_investigation_repo(
    tenant: TenantContext = Depends(current_tenant),
    session: AsyncSession = Depends(get_session),
) -> InvestigationRepository:
    """Tenant-scoped InvestigationRepository for the read endpoints."""
    return InvestigationRepository(session, tenant.tenant_id)


def get_report_repo(
    tenant: TenantContext = Depends(current_tenant),
    session: AsyncSession = Depends(get_session),
) -> ReportRepository:
    """Tenant-scoped ReportRepository for the read endpoints."""
    return ReportRepository(session, tenant.tenant_id)


def get_feedback_service(
    tenant: TenantContext = Depends(current_tenant),
    session: AsyncSession = Depends(get_session),
) -> FeedbackService:
    """Tenant-scoped FeedbackService."""
    return FeedbackService(FeedbackRepository(session, tenant.tenant_id))


@lru_cache(maxsize=1)
def get_embedder() -> EmbeddingProvider:
    """Process-wide embedder (model load is expensive — build once).

    Honours ``AI_SRE_EMBEDDING_PROVIDER``. If ``bge`` is requested but
    ``sentence-transformers`` isn't installed, we fall back to the hashing
    embedder with a warning rather than failing every request.
    """
    settings = get_settings()
    provider = settings.embedding_provider
    if provider == "bge":
        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            logger.warning(
                "embedding.bge_unavailable",
                detail="sentence-transformers not installed; using hashing embedder",
            )
            return HashingEmbedder()
    return build_embedder(provider)


def get_knowledge_service(
    tenant: TenantContext = Depends(current_tenant),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeService:
    """Tenant-scoped KnowledgeService with the process-wide embedder."""
    return KnowledgeService(
        KnowledgeRepository(session, tenant.tenant_id), get_embedder()
    )


def get_backtest_service(
    tenant: TenantContext = Depends(current_tenant),
    session: AsyncSession = Depends(get_session),
    job_queue: JobQueue = Depends(get_job_queue),
) -> BacktestService:
    """Tenant-scoped BacktestService for the backtest + replay endpoints."""
    return BacktestService(
        AlertRepository(session, tenant.tenant_id),
        InvestigationRepository(session, tenant.tenant_id),
        ServiceRepository(session, tenant.tenant_id),
        job_queue,
    )
