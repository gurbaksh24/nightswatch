"""CatalogService — discover and read the subject service's metric catalog.

Composes the tenant-scoped repositories with the connector registry. The
heavy lifting (talking to Prometheus) is delegated to the connector's
``discover_metrics`` ABC method, so this service never imports a concrete
connector — it only knows the generic interface.

See spec ``specs/0005-topology-and-catalog.md``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from ai_sre.connectors.base import ConnectorKind
from ai_sre.connectors.registry import ConnectorRegistry
from ai_sre.core.service.repository import (
    MetricCatalogRepository,
    ServiceRepository,
)
from ai_sre.exceptions import (
    ConnectorError,
    IntegrationError,
    IntegrationNotFound,
    IntegrationUnhealthy,
)
from ai_sre.models.service import MetricCatalogEntry, Service
from ai_sre.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class CatalogRefreshResult:
    """Outcome of a catalog refresh.

    ``status`` is one of:
        * ``ok`` — discovery ran; ``discovered`` is the metric count.
        * ``no_prometheus`` — no usable Prometheus integration; skipped.
        * ``error`` — Prometheus was reachable-ish but the query failed;
          ``error`` carries why. The previous catalog is left untouched.
    """

    discovered: int = 0
    status: str = "ok"
    error: str | None = None


class CatalogService:
    """Discover / read the metric catalog for the tenant's subject service."""

    def __init__(
        self,
        service_repo: ServiceRepository,
        catalog_repo: MetricCatalogRepository,
        connector_registry: ConnectorRegistry,
    ) -> None:
        self.service_repo = service_repo
        self.catalog_repo = catalog_repo
        self.connector_registry = connector_registry

    @property
    def tenant_id(self) -> UUID:
        return self.catalog_repo.tenant_id

    async def refresh_by_id(self, service_id: UUID) -> CatalogRefreshResult | None:
        """Refresh by service id. Returns ``None`` if the service isn't
        owned by this tenant (so the API layer can map to 404)."""
        service = await self.service_repo.get_by_id(service_id)
        if service is None:
            return None
        return await self.refresh(service)

    async def refresh(self, service: Service) -> CatalogRefreshResult:
        """Discover metrics for ``service`` and replace its catalog rows.

        Never raises for the expected failure modes (no Prometheus,
        query error); they're returned as a typed result so callers can
        log/observe without try/except.
        """
        try:
            connector = await self.connector_registry.get(
                self.tenant_id, ConnectorKind.PROMETHEUS
            )
        except (IntegrationNotFound, IntegrationUnhealthy):
            logger.info(
                "catalog.refresh.skipped_no_prometheus",
                tenant_id=str(self.tenant_id),
                service_id=str(service.id),
            )
            return CatalogRefreshResult(status="no_prometheus")
        except IntegrationError as exc:
            logger.warning(
                "catalog.refresh.connector_lookup_failed",
                tenant_id=str(self.tenant_id),
                service_id=str(service.id),
                error=str(exc),
            )
            return CatalogRefreshResult(status="error", error=str(exc))

        try:
            raw = await connector.discover_metrics(
                {"label_selector": service.label_selector}
            )
        except ConnectorError as exc:
            logger.warning(
                "catalog.refresh.discover_failed",
                tenant_id=str(self.tenant_id),
                service_id=str(service.id),
                error=str(exc),
            )
            return CatalogRefreshResult(status="error", error=str(exc))

        count = await self.catalog_repo.replace_for_service(service.id, list(raw))
        logger.info(
            "catalog.refresh.complete",
            tenant_id=str(self.tenant_id),
            service_id=str(service.id),
            discovered=count,
        )
        return CatalogRefreshResult(discovered=count, status="ok")

    async def list_metrics(
        self,
        service_id: UUID,
        *,
        name_filter: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[MetricCatalogEntry] | None:
        """List catalog entries for a service. ``None`` if service missing."""
        service = await self.service_repo.get_by_id(service_id)
        if service is None:
            return None
        return await self.catalog_repo.list_for_service(
            service_id, name_filter=name_filter, limit=limit, offset=offset
        )
