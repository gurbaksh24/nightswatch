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

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sre.config import get_settings
from ai_sre.connectors.registry import ConnectorRegistry
from ai_sre.core.integration.repository import IntegrationRepository
from ai_sre.core.integration.service import IntegrationService
from ai_sre.core.service.repository import ServiceRepository
from ai_sre.core.service.service import ServiceService
from ai_sre.core.tenant.api_key_service import ApiKeyService
from ai_sre.core.tenant.context import TenantContext
from ai_sre.core.tenant.repository import ApiKeyRepository, TenantRepository
from ai_sre.core.tenant.service import TenantService
from ai_sre.db import get_sessionmaker
from ai_sre.queue.base import JobQueue
from ai_sre.queue.procrastinate_queue import ProcrastinateJobQueue
from ai_sre.utils.crypto import EnvelopeEncryptionService

__all__ = [
    "TenantContext",
    "admin_only",
    "current_tenant",
    "get_api_key_service",
    "get_connector_registry",
    "get_integration_service",
    "get_job_queue",
    "get_service_service",
    "get_session",
    "get_tenant_service",
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
