"""Tenant lifecycle service.

Wraps `TenantRepository` with the typed exceptions and logging that the API
layer expects. The repository is the only thing that talks to the database;
service methods compose repository calls into business operations.
"""

from __future__ import annotations

from uuid import UUID

from ai_sre.core.tenant.repository import TenantRepository
from ai_sre.exceptions import TenantNotFound
from ai_sre.models.tenant import Tenant
from ai_sre.utils.logging import get_logger

logger = get_logger(__name__)


class TenantService:
    """Tenant lifecycle: create, read, update."""

    def __init__(self, repo: TenantRepository) -> None:
        self.repo = repo

    async def create(self, *, name: str, slug: str) -> Tenant:
        """Create a tenant. Raises `TenantSlugTaken` on slug conflict."""
        tenant = await self.repo.create(name=name, slug=slug)
        logger.info("tenant.created", tenant_id=str(tenant.id), slug=tenant.slug)
        return tenant

    async def get(self, tenant_id: UUID) -> Tenant | None:
        """Return the tenant by id, or None."""
        return await self.repo.get(tenant_id)

    async def get_by_slug(self, slug: str) -> Tenant | None:
        """Return the tenant by slug, or None."""
        return await self.repo.get_by_slug(slug)

    async def update(self, tenant_id: UUID, *, name: str | None = None) -> Tenant:
        """Update fields on the tenant. Raises `TenantNotFound` if absent."""
        tenant = await self.repo.update(tenant_id, name=name)
        if tenant is None:
            raise TenantNotFound(
                f"Tenant {tenant_id} not found.",
                details={"tenant_id": str(tenant_id)},
            )
        return tenant
