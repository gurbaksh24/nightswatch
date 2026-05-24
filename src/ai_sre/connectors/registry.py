"""Per-tenant connector registry.

Application code calls :meth:`ConnectorRegistry.get` (or the targeted
:meth:`health_check_for`) instead of constructing a connector directly.
The registry:

    * Loads the integration row from the DB.
    * Decrypts the config via :class:`IntegrationService.decrypt_config`.
    * Builds the right concrete :class:`Connector`.
    * Caches the instance per ``(tenant_id, kind)`` for the process lifetime.

Lifetime: created on app/worker startup, kept for the process. Cache is
invalidated when an integration is updated or deleted via
:meth:`invalidate`. The cache is in-process — every worker/API replica
holds its own; that's fine, integrations change rarely.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_sre.connectors.base import Connector, ConnectorHealth, ConnectorKind
from ai_sre.connectors.prometheus.connector import (
    PrometheusConfig,
    PrometheusConnector,
)
from ai_sre.core.integration.repository import IntegrationRepository
from ai_sre.core.integration.service import IntegrationService
from ai_sre.exceptions import (
    IntegrationError,
    IntegrationNotFound,
    IntegrationUnhealthy,
)
from ai_sre.models.integration import Integration
from ai_sre.utils.crypto import EnvelopeEncryptionService
from ai_sre.utils.logging import get_logger

logger = get_logger(__name__)


class ConnectorRegistry:
    """Owns per-tenant connector instances."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        crypto: EnvelopeEncryptionService,
        *,
        query_timeout_seconds: int,
        max_points: int,
        max_series: int,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._crypto = crypto
        self._query_timeout_seconds = query_timeout_seconds
        self._max_points = max_points
        self._max_series = max_series
        # (tenant_id, kind_str) → Connector
        self._cache: dict[tuple[UUID, str], Connector] = {}

    async def get(self, tenant_id: UUID, kind: ConnectorKind) -> Connector:
        """Return a Connector for this tenant + kind, building one on first use.

        Raises:
            IntegrationNotFound: no integration of this kind for this tenant.
            IntegrationUnhealthy: integration exists but is disabled.
            IntegrationError: any other lookup or build failure.
        """
        cache_key = (tenant_id, str(kind))
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        connector = await self._build(tenant_id, kind)
        self._cache[cache_key] = connector
        return connector

    async def health_check_for(
        self, tenant_id: UUID, integration_id: UUID
    ) -> ConnectorHealth:
        """Run a one-off health check against a specific integration.

        Always builds a fresh connector — does not cache. Use this from the
        ``run_health_check`` worker task so a flaky integration doesn't
        poison the cache forever.

        Raises :class:`IntegrationNotFound` if the id isn't owned by this
        tenant.
        """
        async with self._sessionmaker() as session:
            repo = IntegrationRepository(session, tenant_id)
            service = IntegrationService(repo, self._crypto)
            row = await service.get(integration_id)
            if row is None:
                raise IntegrationNotFound(
                    f"Integration {integration_id} not found.",
                    details={"integration_id": str(integration_id)},
                )
            connector = self._build_from_row(row, service)

        try:
            return await connector.health_check()
        finally:
            await self._close(connector)

    async def invalidate(
        self, tenant_id: UUID, kind: ConnectorKind | None = None
    ) -> None:
        """Drop cached connector(s). Closes any owned HTTP clients."""
        to_drop: list[tuple[UUID, str]] = []
        if kind is None:
            to_drop = [k for k in self._cache if k[0] == tenant_id]
        else:
            cache_key = (tenant_id, str(kind))
            if cache_key in self._cache:
                to_drop = [cache_key]
        for k in to_drop:
            conn = self._cache.pop(k, None)
            if conn is not None:
                await self._close(conn)

    async def close_all(self) -> None:
        """Close every cached connector. Call on app shutdown."""
        for conn in list(self._cache.values()):
            await self._close(conn)
        self._cache.clear()

    # ---- internals ----

    async def _build(self, tenant_id: UUID, kind: ConnectorKind) -> Connector:
        """Load the integration row and build the connector."""
        async with self._sessionmaker() as session:
            repo = IntegrationRepository(session, tenant_id)
            rows = await repo.list_all()
            matching = [r for r in rows if r.kind == str(kind)]
            if not matching:
                raise IntegrationNotFound(
                    f"No {kind} integration for tenant {tenant_id}.",
                    details={"tenant_id": str(tenant_id), "kind": str(kind)},
                )
            # MVP: at most one integration per kind per tenant (UNIQUE constraint
            # is on (tenant_id, kind, name) so duplicates by name are possible,
            # but we pick the first deterministic-by-id match).
            row = matching[0]
            if row.status == "disabled":
                raise IntegrationUnhealthy(
                    f"Integration {row.id} is disabled.",
                    details={"integration_id": str(row.id)},
                )
            service = IntegrationService(repo, self._crypto)
            return self._build_from_row(row, service)

    def _build_from_row(
        self,
        row: Integration,
        service: IntegrationService,
    ) -> Connector:
        """Construct the concrete connector for an integration row."""
        if row.kind == ConnectorKind.PROMETHEUS:
            plaintext_config = service.decrypt_config(row)
            config = PrometheusConfig.from_decrypted(
                plaintext_config,
                query_timeout_seconds=self._query_timeout_seconds,
                max_points=self._max_points,
                max_series=self._max_series,
            )
            return PrometheusConnector(config)
        raise IntegrationError(
            f"No connector builder for kind {row.kind!r}.",
            details={"kind": row.kind},
        )

    @staticmethod
    async def _close(connector: Connector) -> None:
        """Close a connector if it exposes an aclose method (PrometheusConnector does)."""
        aclose = getattr(connector, "aclose", None)
        if aclose is not None:
            await aclose()
