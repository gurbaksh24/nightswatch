"""FastAPI dependencies.

The only place where authentication and tenant resolution happen for the API.
Routes declare `tenant: TenantContext = Depends(current_tenant)` to require
auth; admin-only routes use `Depends(admin_only)`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sre.config import get_settings
from ai_sre.db import get_sessionmaker


@dataclass(frozen=True)
class TenantContext:
    """The authenticated tenant for the duration of one request."""

    tenant_id: UUID
    name: str
    slug: str
    api_key_id: UUID


async def get_session() -> AsyncIterator[AsyncSession]:
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def current_tenant(
    authorization: str = Header(..., alias="Authorization"),
    session: AsyncSession = Depends(get_session),
) -> TenantContext:
    """Parse Bearer token, look up the API key, return tenant context.

    Implementation deferred — see spec 0001.
    """
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "auth.invalid_token", "message": "Missing bearer token."},
        )
    # TODO(spec-0001): use ApiKeyService.verify to load the tenant.
    raise NotImplementedError


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
