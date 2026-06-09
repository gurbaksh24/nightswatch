"""Tenant and API-key repositories.

`TenantRepository` is the only repository in `core/` that is **not**
tenant-scoped: tenants are the root, so the repository that creates them
cannot have a tenant filter applied. `ApiKeyRepository` is partially scoped:
tenant-scoped for list/revoke, tenant-agnostic for `find_by_hash` (which is
how authentication bootstraps a tenant context in the first place).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sre.exceptions import TenantSlugTaken
from ai_sre.models.api_key import ApiKey
from ai_sre.models.tenant import Tenant


class TenantRepository:
    """Direct access to the `tenant` table. NOT tenant-scoped."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tenant_id: UUID) -> Tenant | None:
        """Return the tenant by primary key, or None if absent."""
        return await self.session.get(Tenant, tenant_id)

    async def get_by_slug(self, slug: str) -> Tenant | None:
        """Return the tenant by slug, or None if absent."""
        stmt = select(Tenant).where(Tenant.slug == slug)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self) -> Sequence[Tenant]:
        """Return every tenant, oldest first.

        Root-level and intentionally NOT tenant-scoped — used by cross-tenant
        maintenance jobs (e.g. the periodic discovery refresh in spec 0005)
        that legitimately need to fan out over all tenants.
        """
        stmt = select(Tenant).order_by(Tenant.created_at, Tenant.id)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def create(self, *, name: str, slug: str) -> Tenant:
        """Insert a new tenant. Raises `TenantSlugTaken` on slug conflict."""
        tenant = Tenant(name=name, slug=slug, status="active")
        self.session.add(tenant)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise TenantSlugTaken(
                f"Slug '{slug}' is already taken.",
                details={"slug": slug},
            ) from exc
        return tenant

    async def update(self, tenant_id: UUID, *, name: str | None = None) -> Tenant | None:
        """Update an existing tenant; return None if not found."""
        tenant = await self.get(tenant_id)
        if tenant is None:
            return None
        if name is not None:
            tenant.name = name
        await self.session.flush()
        # `updated_at` has onupdate=func.now(); after the UPDATE flush it's
        # expired, so refresh it within the async greenlet. Otherwise the
        # route's Pydantic serialization triggers a lazy load in a sync
        # context -> MissingGreenlet. (Mirrors ServiceRepository/Integration.)
        await self.session.refresh(tenant)
        return tenant


class ApiKeyRepository:
    """Storage for API keys. Lookup-by-hash is global; list/revoke is scoped."""

    def __init__(self, session: AsyncSession, tenant_id: UUID | None = None) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def find_by_hash(self, key_hash: str) -> ApiKey | None:
        """Return the active row for a given SHA-256 hash, or None."""
        stmt = select(ApiKey).where(ApiKey.key_hash == key_hash)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_tenant(self, tenant_id: UUID) -> Sequence[ApiKey]:
        """List all API keys for a tenant, newest first.

        `created_at` is driven by ``func.now()`` server-side, which returns
        transaction-start time and can tie when two rows are written close
        together. Tiebreak on ``id`` (UUIDv7, time-ordered) so the ordering
        stays stable across runs.
        """
        stmt = (
            select(ApiKey)
            .where(ApiKey.tenant_id == tenant_id)
            .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def create(
        self, *, tenant_id: UUID, name: str, key_hash: str, prefix: str
    ) -> ApiKey:
        """Insert a new key row. The plaintext key is never persisted."""
        row = ApiKey(
            tenant_id=tenant_id,
            name=name,
            key_hash=key_hash,
            prefix=prefix,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def revoke(self, tenant_id: UUID, key_id: UUID) -> None:
        """Mark a key revoked. Silent no-op if missing or on a different tenant."""
        row = await self.session.get(ApiKey, key_id)
        if row is None or row.tenant_id != tenant_id:
            return
        if row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
            await self.session.flush()

    async def mark_used(self, api_key_id: UUID) -> None:
        """Update `last_used_at` to now. Best-effort; missing key is silent."""
        row = await self.session.get(ApiKey, api_key_id)
        if row is None:
            return
        row.last_used_at = datetime.now(UTC)
        await self.session.flush()
