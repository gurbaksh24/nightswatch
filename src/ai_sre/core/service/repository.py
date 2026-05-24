"""ServiceRepository — tenant-scoped CRUD over the ``service`` table.

Inherits :class:`TenantScopedRepository`, so every query automatically
carries ``WHERE tenant_id = :tenant_id``. The MVP enforces one service per
tenant via a UNIQUE constraint on ``tenant_id``; the repository surfaces
that as :class:`ServiceAlreadyExists`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sre.core._base.repository import TenantScopedRepository
from ai_sre.exceptions import ServiceAlreadyExists
from ai_sre.models.service import Service


class ServiceRepository(TenantScopedRepository[Service]):
    """CRUD for :class:`Service`, scoped to a single tenant."""

    model = Service

    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        super().__init__(session, tenant_id)

    async def get_by_id(self, service_id: UUID) -> Service | None:
        """Return the service by id, scoped to this tenant, or ``None``."""
        return await self.get(service_id)

    async def get_for_tenant(self) -> Service | None:
        """Return the tenant's single service, if any.

        MVP enforces one service per tenant. This helper exists so callers
        don't need to know the id ahead of time (e.g. "does this tenant
        have a service yet?").
        """
        rows = await self.list(limit=1)
        return rows[0] if rows else None

    async def create(
        self,
        *,
        name: str,
        label_selector: dict[str, str],
        ownership: dict[str, Any] | None,
        slo_config: dict[str, Any] | None,
    ) -> Service:
        """Insert the tenant's service. Raises :class:`ServiceAlreadyExists`
        if one already exists (UNIQUE constraint on ``tenant_id``)."""
        row = Service(
            tenant_id=self.tenant_id,
            name=name,
            label_selector=label_selector,
            ownership=ownership,
            slo_config=slo_config,
        )
        self.session.add(row)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ServiceAlreadyExists(
                "This tenant already has a registered service.",
                details={"tenant_id": str(self.tenant_id)},
            ) from exc
        await self.session.refresh(row)
        return row

    async def update_metadata(
        self,
        service_id: UUID,
        *,
        name: str | None = None,
        ownership: dict[str, Any] | None = None,
        slo_config: dict[str, Any] | None = None,
    ) -> Service | None:
        """Update mutable fields. Returns the updated row, or ``None`` if
        no service with this id is owned by this tenant.

        ``label_selector`` is intentionally NOT updateable — per spec 0004,
        changing the selector means delete + re-register.
        """
        row = await self.get(service_id)
        if row is None:
            return None
        if name is not None:
            row.name = name
        if ownership is not None:
            row.ownership = ownership
        if slo_config is not None:
            row.slo_config = slo_config
        await self.session.flush()
        await self.session.refresh(row)
        return row
