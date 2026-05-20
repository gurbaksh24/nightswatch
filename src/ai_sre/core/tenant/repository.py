"""Tenant and API-key repositories.

Note: `TenantRepository` is *not* tenant-scoped (tenants are the root), so it
does not inherit from `TenantScopedRepository`. `ApiKeyRepository` IS scoped.

Spec 0001 implements these.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


class TenantRepository:
    """Direct access to the tenants table. NOT tenant-scoped — this is the
    boundary that creates tenants in the first place.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tenant_id: UUID) -> object | None:
        # TODO(spec-0001)
        raise NotImplementedError

    async def get_by_slug(self, slug: str) -> object | None:
        raise NotImplementedError

    async def create(self, *, name: str, slug: str) -> object:
        raise NotImplementedError


class ApiKeyRepository:
    """Tenant-scoped only for list/revoke. Verification is global (by hash).

    Splitting cleanly: a `verify_global(key_hash)` helper is on this class but
    does not require a tenant context — it's how authentication bootstraps.
    """

    def __init__(self, session: AsyncSession, tenant_id: UUID | None = None) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def find_by_hash(self, key_hash: str) -> object | None:
        """Lookup an active API key by its SHA-256 hash. Tenant-agnostic."""
        raise NotImplementedError

    async def list_for_tenant(self, tenant_id: UUID) -> list[object]:
        raise NotImplementedError

    async def create(
        self, *, tenant_id: UUID, name: str, key_hash: str, prefix: str
    ) -> object:
        raise NotImplementedError

    async def revoke(self, tenant_id: UUID, key_id: UUID) -> None:
        raise NotImplementedError
