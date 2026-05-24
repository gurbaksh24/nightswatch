"""Unit tests for :class:`ServiceService`.

Use in-memory fakes for the repository + connector registry so the service
logic is exercised without a DB or real Prometheus. The repository's real
behaviour is exercised in ``tests/integration/test_service_routes.py``.
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
from ai_sre.core.service.service import ServiceService
from ai_sre.exceptions import (
    ConnectorTimeout,
    IntegrationNotFound,
    ServiceAlreadyExists,
)
from ai_sre.models.service import Service
from ai_sre.utils.ids import new_id

# ---- Fakes ----


class _FakeServiceRepository:
    """In-memory stand-in for :class:`ServiceRepository`.

    Mirrors the UNIQUE(tenant_id) constraint of the real table: a second
    ``create`` for the same tenant raises :class:`ServiceAlreadyExists`.
    """

    def __init__(self, tenant_id: UUID) -> None:
        self.tenant_id = tenant_id
        self._row: Service | None = None

    async def get_by_id(self, service_id: UUID) -> Service | None:
        if self._row is None or self._row.id != service_id:
            return None
        return self._row

    async def get_for_tenant(self) -> Service | None:
        return self._row

    async def create(
        self,
        *,
        name: str,
        label_selector: dict[str, str],
        ownership: dict[str, Any] | None,
        slo_config: dict[str, Any] | None,
    ) -> Service:
        if self._row is not None:
            raise ServiceAlreadyExists(
                "This tenant already has a registered service.",
                details={"tenant_id": str(self.tenant_id)},
            )
        row = Service(
            id=new_id(),
            tenant_id=self.tenant_id,
            name=name,
            label_selector=label_selector,
            ownership=ownership,
            slo_config=slo_config,
        )
        self._row = row
        return row

    async def update_metadata(
        self,
        service_id: UUID,
        *,
        name: str | None = None,
        ownership: dict[str, Any] | None = None,
        slo_config: dict[str, Any] | None = None,
    ) -> Service | None:
        if self._row is None or self._row.id != service_id:
            return None
        if name is not None:
            self._row.name = name
        if ownership is not None:
            self._row.ownership = ownership
        if slo_config is not None:
            self._row.slo_config = slo_config
        return self._row


class _FakePromConnector(Connector):
    """Fake Prometheus connector returning a fixed ``series_count``.

    Optionally raises a configurable exception to simulate timeouts /
    transport errors.
    """

    kind = ConnectorKind.PROMETHEUS

    def __init__(
        self,
        *,
        series_count: int = 1,
        success: bool = True,
        error: str | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.series_count = series_count
        self.success = success
        self.error = error
        self.raises = raises
        self.queries: list[str] = []

    async def health_check(self) -> ConnectorHealth:
        return ConnectorHealth(healthy=True)

    async def discover_topology(self, service: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    async def discover_metrics(
        self, service: dict[str, Any]
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def query(self, query: ConnectorQuery) -> ConnectorResult:
        from ai_sre.connectors.base import PromQLQuery, RawPromQL

        assert isinstance(query, PromQLQuery)
        assert isinstance(query.intent, RawPromQL)
        self.queries.append(query.intent.query)
        if self.raises is not None:
            raise self.raises
        return ConnectorResult(
            success=self.success,
            series_count=self.series_count,
            error=self.error,
            data={"result": []},
        )


class _StubRegistry(ConnectorRegistry):
    """ConnectorRegistry stub returning a preset connector (or raising).

    The real registry's DB/crypto plumbing isn't relevant to ServiceService
    tests — only ``get(...)`` is consulted.
    """

    def __init__(
        self,
        connector: Connector | None = None,
        *,
        raise_on_get: Exception | None = None,
    ) -> None:
        self._connector = connector
        self._raise = raise_on_get

    async def get(self, tenant_id: UUID, kind: ConnectorKind) -> Connector:
        if self._raise is not None:
            raise self._raise
        assert self._connector is not None
        return self._connector


def _build(
    *,
    connector: Connector | None = None,
    raise_on_get: Exception | None = None,
) -> tuple[ServiceService, _FakeServiceRepository, _StubRegistry]:
    tenant_id = new_id()
    repo = _FakeServiceRepository(tenant_id)
    registry = _StubRegistry(connector=connector, raise_on_get=raise_on_get)
    service = ServiceService(repo, registry)  # type: ignore[arg-type]
    return service, repo, registry


# ---- register ----


@pytest.mark.unit
@pytest.mark.asyncio
async def test_register_with_matching_series_is_clean() -> None:
    """Selector matches ≥1 series → no warnings, no validation_pending."""
    conn = _FakePromConnector(series_count=3)
    service, _, _ = _build(connector=conn)

    result = await service.register(
        name="checkout",
        label_selector={"app": "checkout", "env": "prod"},
    )

    assert result.warnings == []
    assert result.service.slo_config is None
    # Confirm the probe was built deterministically (sorted keys, escaped).
    assert conn.queries == ['up{app="checkout",env="prod"}']


@pytest.mark.unit
@pytest.mark.asyncio
async def test_register_with_zero_series_returns_warning() -> None:
    """Selector matches 0 series → service persisted, but with a warning."""
    conn = _FakePromConnector(series_count=0)
    service, _, _ = _build(connector=conn)

    result = await service.register(
        name="ghost", label_selector={"app": "ghost"}
    )

    assert len(result.warnings) == 1
    assert "0 series" in result.warnings[0]
    # Zero matches is NOT "validation pending" — Prometheus answered.
    assert result.service.slo_config is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_register_with_no_prometheus_sets_validation_pending() -> None:
    """No Prometheus integration → validation_pending=True, no warning."""
    service, _, _ = _build(
        raise_on_get=IntegrationNotFound(
            "No prometheus integration for tenant.",
        )
    )

    result = await service.register(
        name="checkout", label_selector={"app": "checkout"}
    )

    assert result.warnings == []
    assert result.service.slo_config == {"validation_pending": True}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_register_when_prometheus_query_times_out() -> None:
    """Prom present but the validation query timed out → validation_pending
    and a warning that explains why."""
    conn = _FakePromConnector(raises=ConnectorTimeout("slow"))
    service, _, _ = _build(connector=conn)

    result = await service.register(
        name="checkout", label_selector={"app": "checkout"}
    )

    assert result.service.slo_config == {"validation_pending": True}
    assert len(result.warnings) == 1
    assert "timeout" in result.warnings[0].lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_register_when_prometheus_returns_failure() -> None:
    """ConnectorResult(success=False) → validation_pending + warning."""
    conn = _FakePromConnector(success=False, error="HTTP 401")
    service, _, _ = _build(connector=conn)

    result = await service.register(
        name="checkout", label_selector={"app": "checkout"}
    )

    assert result.service.slo_config == {"validation_pending": True}
    assert any("401" in w for w in result.warnings)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_register_duplicate_raises_service_already_exists() -> None:
    """MVP: one service per tenant."""
    conn = _FakePromConnector(series_count=1)
    service, _, _ = _build(connector=conn)

    await service.register(name="a", label_selector={"app": "a"})
    with pytest.raises(ServiceAlreadyExists):
        await service.register(name="b", label_selector={"app": "b"})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_register_with_ownership_metadata_round_trips() -> None:
    conn = _FakePromConnector(series_count=1)
    service, _, _ = _build(connector=conn)

    result = await service.register(
        name="checkout",
        label_selector={"app": "checkout"},
        ownership={"team": "payments", "slack": "#payments"},
    )

    assert result.service.ownership == {
        "team": "payments",
        "slack": "#payments",
    }


# ---- get ----


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_returns_none_for_unknown_id() -> None:
    service, _, _ = _build(connector=_FakePromConnector())
    assert await service.get(new_id()) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_returns_row_after_registration() -> None:
    service, _, _ = _build(connector=_FakePromConnector(series_count=1))
    result = await service.register(name="a", label_selector={"app": "a"})
    fetched = await service.get(result.service.id)
    assert fetched is not None
    assert fetched.id == result.service.id


# ---- validate_label_selector ----


@pytest.mark.unit
@pytest.mark.asyncio
async def test_validate_label_selector_no_prometheus() -> None:
    service, _, _ = _build(
        raise_on_get=IntegrationNotFound("no prom"),
    )
    v = await service.validate_label_selector({"app": "x"})
    assert v.has_prometheus is False
    assert v.series_count == 0
    assert v.error is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_validate_label_selector_escapes_quotes_and_backslashes() -> None:
    """Defence against label-value injection — special chars are escaped."""
    conn = _FakePromConnector(series_count=0)
    service, _, _ = _build(connector=conn)

    await service.validate_label_selector(
        {"env": 'prod"injection', "path": "a\\b"}
    )

    sent = conn.queries[0]
    # " is escaped as \", \ is escaped as \\.
    assert 'env="prod\\"injection"' in sent
    assert 'path="a\\\\b"' in sent
