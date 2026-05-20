"""Tenant service.

Implementation tracked by spec 0001. The shapes below are the contract;
filling them in is the spec's job.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from ai_sre.core.tenant.repository import TenantRepository
    from ai_sre.models.tenant import Tenant


class TenantService:
    """Tenant lifecycle: create, read, update, soft-delete."""

    def __init__(self, repo: TenantRepository) -> None:
        self.repo = repo

    async def create(self, *, name: str, slug: str) -> Tenant:
        """Create a tenant. Raises TenantSlugTaken on slug conflict."""
        raise NotImplementedError

    async def get(self, tenant_id: UUID) -> Tenant | None:
        raise NotImplementedError

    async def get_by_slug(self, slug: str) -> Tenant | None:
        raise NotImplementedError

    async def update(self, tenant_id: UUID, *, name: str | None = None) -> Tenant:
        raise NotImplementedError
