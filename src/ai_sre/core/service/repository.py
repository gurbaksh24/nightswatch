"""ServiceRepository — tenant-scoped CRUD over the ``service`` table.

Inherits :class:`TenantScopedRepository`, so every query automatically
carries ``WHERE tenant_id = :tenant_id``. The MVP enforces one service per
tenant via a UNIQUE constraint on ``tenant_id``; the repository surfaces
that as :class:`ServiceAlreadyExists`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sre.core._base.repository import TenantScopedRepository
from ai_sre.exceptions import ServiceAlreadyExists
from ai_sre.models.service import (
    MetricCatalogEntry,
    Service,
    ServiceDependency,
)


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


class MetricCatalogRepository(TenantScopedRepository[MetricCatalogEntry]):
    """Tenant-scoped CRUD for :class:`MetricCatalogEntry`.

    Catalog refresh is delete-and-replace: there's no user-owned state on a
    metric entry (unlike dependencies), so re-discovering from scratch each
    time keeps the catalog a faithful snapshot.
    """

    model = MetricCatalogEntry

    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        super().__init__(session, tenant_id)

    async def replace_for_service(
        self, service_id: UUID, entries: list[dict[str, Any]]
    ) -> int:
        """Replace all catalog rows for a service. Returns the count written.

        ``entries`` are the dicts produced by ``Connector.discover_metrics``
        (``metric_name``, ``metric_type``, ``labels``, ``unit``,
        ``help_text``). ``last_seen_at`` is stamped now on every row.
        """
        now = datetime.now(UTC)
        await self.session.execute(
            delete(MetricCatalogEntry).where(
                MetricCatalogEntry.tenant_id == self.tenant_id,
                MetricCatalogEntry.service_id == service_id,
            )
        )
        rows = [
            MetricCatalogEntry(
                tenant_id=self.tenant_id,
                service_id=service_id,
                metric_name=entry["metric_name"],
                metric_type=entry.get("metric_type", "unknown"),
                labels=entry.get("labels", {}),
                unit=entry.get("unit"),
                help_text=entry.get("help_text"),
                last_seen_at=now,
            )
            for entry in entries
        ]
        self.session.add_all(rows)
        await self.session.flush()
        return len(rows)

    async def list_for_service(
        self,
        service_id: UUID,
        *,
        name_filter: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[MetricCatalogEntry]:
        """List catalog entries for a service, ordered by metric name.

        ``name_filter`` is a case-insensitive substring match on
        ``metric_name``.
        """
        stmt = self._scoped(select(MetricCatalogEntry)).where(
            MetricCatalogEntry.service_id == service_id
        )
        if name_filter:
            stmt = stmt.where(MetricCatalogEntry.metric_name.ilike(f"%{name_filter}%"))
        stmt = stmt.order_by(MetricCatalogEntry.metric_name).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return result.scalars().all()


class ServiceDependencyRepository(TenantScopedRepository[ServiceDependency]):
    """Tenant-scoped CRUD for :class:`ServiceDependency`.

    Topology refresh upserts on ``(service_id, direction, name)``: existing
    edges get a fresh ``last_seen_at`` (and metadata) while preserving the
    user's ``confirmed_by_user`` flag; new edges are inserted unconfirmed.
    Edges that stop appearing are *kept* (stale ``last_seen_at``), never
    deleted — they may have been user-confirmed.
    """

    model = ServiceDependency

    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        super().__init__(session, tenant_id)

    async def list_for_service(
        self, service_id: UUID
    ) -> Sequence[ServiceDependency]:
        """All dependency edges for a service, ordered (direction, name)."""
        stmt = (
            self._scoped(select(ServiceDependency))
            .where(ServiceDependency.service_id == service_id)
            .order_by(ServiceDependency.direction, ServiceDependency.name)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def upsert_discovered(
        self, service_id: UUID, deps: list[dict[str, Any]]
    ) -> int:
        """Upsert discovered edges by ``(direction, name)``. Returns the
        number of edges seen this round (not the total stored).

        ``deps`` carry ``direction``, ``name``, and optional ``via_label``.
        """
        now = datetime.now(UTC)
        existing = {
            (row.direction, row.name): row
            for row in await self.list_for_service(service_id)
        }
        for dep in deps:
            key = (dep["direction"], dep["name"])
            metadata = {"via_label": dep.get("via_label")}
            row = existing.get(key)
            if row is None:
                self.session.add(
                    ServiceDependency(
                        tenant_id=self.tenant_id,
                        service_id=service_id,
                        direction=dep["direction"],
                        name=dep["name"],
                        extra_metadata=metadata,
                        confirmed_by_user=False,
                        last_seen_at=now,
                    )
                )
            else:
                # Preserve confirmed_by_user; just refresh observation state.
                row.extra_metadata = metadata
                row.last_seen_at = now
        await self.session.flush()
        return len(deps)

    async def set_confirmation(
        self, dependency_id: UUID, confirmed: bool
    ) -> ServiceDependency | None:
        """Set ``confirmed_by_user`` on one edge. ``None`` if not owned here."""
        row = await self.get(dependency_id)
        if row is None:
            return None
        row.confirmed_by_user = confirmed
        await self.session.flush()
        return row
