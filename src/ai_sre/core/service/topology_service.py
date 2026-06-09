"""TopologyService — discover and read the subject service's dependencies.

Like :class:`~ai_sre.core.service.catalog_service.CatalogService`, this
composes tenant-scoped repositories with the connector registry and only
ever calls the generic ``Connector.discover_topology`` ABC method.

Refresh is an *upsert*, not a replace: a dependency a user previously
confirmed is kept (with a stale ``last_seen_at``) even if a later refresh
no longer sees it. See spec ``specs/0005-topology-and-catalog.md``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from ai_sre.connectors.base import ConnectorKind
from ai_sre.connectors.registry import ConnectorRegistry
from ai_sre.core.service.repository import (
    ServiceDependencyRepository,
    ServiceRepository,
)
from ai_sre.exceptions import (
    ConnectorError,
    IntegrationError,
    IntegrationNotFound,
    IntegrationUnhealthy,
)
from ai_sre.models.service import Service, ServiceDependency
from ai_sre.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class TopologyRefreshResult:
    """Outcome of a topology refresh. See
    :class:`~ai_sre.core.service.catalog_service.CatalogRefreshResult` for
    the ``status`` semantics."""

    discovered: int = 0
    status: str = "ok"
    error: str | None = None


class TopologyService:
    """Discover / read the dependency graph for the tenant's subject service."""

    def __init__(
        self,
        service_repo: ServiceRepository,
        dependency_repo: ServiceDependencyRepository,
        connector_registry: ConnectorRegistry,
    ) -> None:
        self.service_repo = service_repo
        self.dependency_repo = dependency_repo
        self.connector_registry = connector_registry

    @property
    def tenant_id(self) -> UUID:
        return self.dependency_repo.tenant_id

    async def refresh_by_id(self, service_id: UUID) -> TopologyRefreshResult | None:
        """Refresh by service id. ``None`` if the service isn't owned here."""
        service = await self.service_repo.get_by_id(service_id)
        if service is None:
            return None
        return await self.refresh(service)

    async def refresh(self, service: Service) -> TopologyRefreshResult:
        """Discover dependency edges for ``service`` and upsert them."""
        try:
            connector = await self.connector_registry.get(
                self.tenant_id, ConnectorKind.PROMETHEUS
            )
        except (IntegrationNotFound, IntegrationUnhealthy):
            logger.info(
                "topology.refresh.skipped_no_prometheus",
                tenant_id=str(self.tenant_id),
                service_id=str(service.id),
            )
            return TopologyRefreshResult(status="no_prometheus")
        except IntegrationError as exc:
            logger.warning(
                "topology.refresh.connector_lookup_failed",
                tenant_id=str(self.tenant_id),
                service_id=str(service.id),
                error=str(exc),
            )
            return TopologyRefreshResult(status="error", error=str(exc))

        try:
            topo = await connector.discover_topology(
                {"label_selector": service.label_selector}
            )
        except ConnectorError as exc:
            logger.warning(
                "topology.refresh.discover_failed",
                tenant_id=str(self.tenant_id),
                service_id=str(service.id),
                error=str(exc),
            )
            return TopologyRefreshResult(status="error", error=str(exc))

        deps: list[dict[str, Any]] = [
            {"direction": "upstream", **edge} for edge in topo.get("upstream", [])
        ] + [
            {"direction": "downstream", **edge}
            for edge in topo.get("downstream", [])
        ]
        count = await self.dependency_repo.upsert_discovered(service.id, deps)
        logger.info(
            "topology.refresh.complete",
            tenant_id=str(self.tenant_id),
            service_id=str(service.id),
            discovered=count,
        )
        return TopologyRefreshResult(discovered=count, status="ok")

    async def get(self, service_id: UUID) -> Sequence[ServiceDependency] | None:
        """All dependency edges for a service. ``None`` if service missing."""
        service = await self.service_repo.get_by_id(service_id)
        if service is None:
            return None
        return await self.dependency_repo.list_for_service(service_id)

    async def confirm(
        self, service_id: UUID, edits: list[tuple[UUID, bool]]
    ) -> Sequence[ServiceDependency] | None:
        """Apply ``confirmed_by_user`` edits, then return the full topology.

        ``None`` if the service isn't owned by this tenant. Edits naming a
        dependency not owned here are silently skipped (the scoped repo
        returns ``None`` for them).
        """
        service = await self.service_repo.get_by_id(service_id)
        if service is None:
            return None
        for dependency_id, confirmed in edits:
            await self.dependency_repo.set_confirmation(dependency_id, confirmed)
        return await self.dependency_repo.list_for_service(service_id)
