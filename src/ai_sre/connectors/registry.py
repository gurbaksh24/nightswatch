"""Per-tenant connector registry.

The orchestrator and tools call `connector_for(tenant_id, kind)` rather than
instantiating connectors directly. The registry holds connections, caches
clients, and is the single owner of integration credentials in memory.

Lifetime: created on app startup, lives for the process lifetime. Connectors
are lazily instantiated per (tenant, kind) and cached.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from ai_sre.exceptions import IntegrationError

if TYPE_CHECKING:
    from ai_sre.connectors.base import Connector, ConnectorKind


class ConnectorRegistry:
    """Owns per-tenant connector instances."""

    def __init__(self) -> None:
        # (tenant_id, kind) → Connector
        self._cache: dict[tuple[UUID, str], Connector] = {}

    async def get(self, tenant_id: UUID, kind: ConnectorKind) -> Connector:
        """Return a Connector for this tenant + kind, creating one if needed.

        Raises IntegrationError if no integration of that kind is registered.
        """
        cache_key = (tenant_id, str(kind))
        if cache_key in self._cache:
            return self._cache[cache_key]

        # TODO(spec-NNNN: connector-registry):
        #   1. Load integration row from DB (decrypt config).
        #   2. Build the right concrete Connector for `kind`.
        #   3. Cache and return.
        raise IntegrationError(
            f"No connector registered for tenant {tenant_id} kind {kind}",
            details={"tenant_id": str(tenant_id), "kind": str(kind)},
        )

    async def invalidate(self, tenant_id: UUID, kind: ConnectorKind | None = None) -> None:
        """Drop cached connector(s) — called when an integration is updated."""
        if kind is None:
            self._cache = {k: v for k, v in self._cache.items() if k[0] != tenant_id}
        else:
            self._cache.pop((tenant_id, str(kind)), None)
