"""Unit tests for the query_newrelic tool handler (spec 0019)."""

from __future__ import annotations

from typing import Any

import pytest

from ai_sre.connectors.base import (
    Connector,
    ConnectorHealth,
    ConnectorKind,
    ConnectorQuery,
    ConnectorResult,
    NRQLQuery,
)
from ai_sre.core.investigation.budget import Budget
from ai_sre.core.investigation.context import InvestigationContext
from ai_sre.core.tenant.context import TenantContext
from ai_sre.exceptions import IntegrationNotFound
from ai_sre.llm.tools.query_newrelic import QUERY_NEWRELIC, query_newrelic_handler
from ai_sre.utils.ids import new_id

pytestmark = pytest.mark.unit


class _FakeNewRelicConnector(Connector):
    kind = ConnectorKind.NEWRELIC

    def __init__(self, result: ConnectorResult) -> None:
        self._result = result
        self.queries: list[ConnectorQuery] = []

    async def health_check(self) -> ConnectorHealth:
        return ConnectorHealth(healthy=True)

    async def discover_topology(self, service: dict[str, Any]) -> dict[str, Any]:
        return {"upstream": [], "downstream": []}

    async def discover_metrics(self, service: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    async def query(self, query: ConnectorQuery) -> ConnectorResult:
        self.queries.append(query)
        return self._result


class _FakeRegistry:
    def __init__(self, connector: Connector | None) -> None:
        self._connector = connector

    async def get(self, tenant_id: Any, kind: ConnectorKind) -> Connector:
        if self._connector is None:
            raise IntegrationNotFound("no newrelic")
        assert kind == ConnectorKind.NEWRELIC
        return self._connector


def _ctx(registry: Any) -> InvestigationContext:
    ctx = InvestigationContext(
        tenant=TenantContext(tenant_id=new_id(), name="t", slug="t", api_key_id=new_id()),
        investigation_id=new_id(),
        alert={},
        service={},
        dependencies={},
        metric_catalog={},
        budget=Budget(),
    )
    ctx.connector_registry = registry  # type: ignore[assignment]
    return ctx


@pytest.mark.asyncio
async def test_happy_path_trims_and_reports() -> None:
    rows = [{"v": i} for i in range(30)]
    connector = _FakeNewRelicConnector(
        ConnectorResult(
            success=True,
            data={"nrql": "SELECT ...", "results": rows, "truncated": False},
            series_count=30,
        )
    )
    out = await query_newrelic_handler(
        {"intent_kind": "rate_over_window", "metric": "errors"}, _ctx(_FakeRegistry(connector))
    )
    assert out["series_count"] == 30
    assert len(out["results"]) == 25 and out["truncated"]
    assert isinstance(connector.queries[0], NRQLQuery)


@pytest.mark.asyncio
async def test_no_integration_and_invalid_intent() -> None:
    out = await query_newrelic_handler(
        {"intent_kind": "rate_over_window", "metric": "m"}, _ctx(_FakeRegistry(None))
    )
    assert out["error"] == "no_newrelic"

    out = await query_newrelic_handler({"intent_kind": "nope"}, _ctx(_FakeRegistry(None)))
    assert out["error"] == "invalid_intent"


@pytest.mark.asyncio
async def test_failed_query_surfaces_as_data() -> None:
    connector = _FakeNewRelicConnector(ConnectorResult(success=False, error="NRQL Syntax Error"))
    out = await query_newrelic_handler(
        {"intent_kind": "raw_nrql", "query": "SELECT broken"}, _ctx(_FakeRegistry(connector))
    )
    assert out == {"error": "query_failed", "detail": "NRQL Syntax Error"}


def test_tool_spec_registered_for_llm_stages() -> None:
    assert QUERY_NEWRELIC.name == "query_newrelic"
    assert QUERY_NEWRELIC.allowed_stages == frozenset({"hypothesis", "validation"})
    assert "intent_kind" in QUERY_NEWRELIC.input_schema["properties"]
