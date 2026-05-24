"""Unit tests for :class:`ConnectorRegistry`.

The registry's DB-loading path is exercised end-to-end in
``tests/integration/test_integration_health_check.py``. These tests focus
on the pure-Python caching and invalidation semantics, using a fake
sessionmaker that's only consulted when the registry calls into the DB.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from ai_sre.connectors.base import (
    Connector,
    ConnectorHealth,
    ConnectorKind,
    ConnectorQuery,
    ConnectorResult,
)
from ai_sre.connectors.registry import ConnectorRegistry
from ai_sre.utils.crypto import EnvelopeEncryptionService, random_base64_key
from ai_sre.utils.ids import new_id


class _FakeConnector(Connector):
    """Bare-bones connector that records how many times health_check ran."""

    kind = ConnectorKind.PROMETHEUS

    def __init__(self) -> None:
        self.health_calls = 0

    async def health_check(self) -> ConnectorHealth:
        self.health_calls += 1
        return ConnectorHealth(healthy=True, latency_ms=1)

    async def discover_topology(self, service: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    async def discover_metrics(
        self, service: dict[str, Any]
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def query(self, query: ConnectorQuery) -> ConnectorResult:
        raise NotImplementedError


def _make_registry() -> ConnectorRegistry:
    """Build a registry whose internal ``_build`` is patched to hand out
    fresh fake connectors. The sessionmaker is never used in this path, so
    we pass a placeholder.
    """

    class _Stub(ConnectorRegistry):
        async def _build(
            self, tenant_id: UUID, kind: ConnectorKind
        ) -> Connector:
            return _FakeConnector()

    # The sessionmaker is unused along the _build → returns _FakeConnector
    # path. mypy would normally insist on `async_sessionmaker[AsyncSession]`;
    # passing `None` keeps the test self-contained.
    return _Stub(
        sessionmaker=None,  # type: ignore[arg-type]
        crypto=EnvelopeEncryptionService(random_base64_key()),
        query_timeout_seconds=10,
        max_points=10_000,
        max_series=10_000,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_caches_per_tenant_and_kind() -> None:
    registry = _make_registry()
    tenant_a = new_id()

    first = await registry.get(tenant_a, ConnectorKind.PROMETHEUS)
    second = await registry.get(tenant_a, ConnectorKind.PROMETHEUS)
    assert first is second


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_returns_different_instances_per_tenant() -> None:
    registry = _make_registry()
    a = new_id()
    b = new_id()

    conn_a = await registry.get(a, ConnectorKind.PROMETHEUS)
    conn_b = await registry.get(b, ConnectorKind.PROMETHEUS)
    assert conn_a is not conn_b


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalidate_drops_cached_instance() -> None:
    registry = _make_registry()
    tenant_a = new_id()

    first = await registry.get(tenant_a, ConnectorKind.PROMETHEUS)
    await registry.invalidate(tenant_a, ConnectorKind.PROMETHEUS)
    second = await registry.get(tenant_a, ConnectorKind.PROMETHEUS)
    assert first is not second


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalidate_all_drops_every_kind_for_tenant() -> None:
    registry = _make_registry()
    tenant_a = new_id()
    tenant_b = new_id()

    a_first = await registry.get(tenant_a, ConnectorKind.PROMETHEUS)
    b_first = await registry.get(tenant_b, ConnectorKind.PROMETHEUS)

    await registry.invalidate(tenant_a)  # tenant-wide invalidation

    a_second = await registry.get(tenant_a, ConnectorKind.PROMETHEUS)
    b_second = await registry.get(tenant_b, ConnectorKind.PROMETHEUS)

    assert a_second is not a_first  # rebuilt
    assert b_second is b_first  # unaffected


@pytest.mark.unit
@pytest.mark.asyncio
async def test_close_all_clears_the_cache() -> None:
    registry = _make_registry()
    tenant_a = new_id()

    first = await registry.get(tenant_a, ConnectorKind.PROMETHEUS)
    await registry.close_all()
    second = await registry.get(tenant_a, ConnectorKind.PROMETHEUS)
    assert first is not second
