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

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sre.config import get_settings
from ai_sre.core.tenant.api_key_service import ApiKeyService
from ai_sre.core.tenant.context import TenantContext
from ai_sre.core.tenant.repository import ApiKeyRepository, TenantRepository
from ai_sre.core.tenant.service import TenantService
from ai_sre.db import get_sessionmaker

__all__ = [
    "TenantContext",
    "admin_only",
    "current_tenant",
    "get_api_key_service",
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
