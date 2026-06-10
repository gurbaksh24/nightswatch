"""Unit tests for the concrete tool handlers (spec 0009)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from ai_sre.connectors.base import ConnectorKind, ConnectorResult
from ai_sre.core.investigation.budget import Budget
from ai_sre.core.investigation.context import InvestigationContext
from ai_sre.core.tenant.context import TenantContext
from ai_sre.exceptions import ConnectorTimeout, IntegrationNotFound
from ai_sre.llm.tools.get_alert_details import get_alert_details_handler
from ai_sre.llm.tools.get_service_dependencies import get_service_dependencies_handler
from ai_sre.llm.tools.list_metric_names import list_metric_names_handler
from ai_sre.llm.tools.query_prometheus import query_prometheus_handler
from ai_sre.utils.ids import new_id


class _FakeConnector:
    def __init__(self, result: ConnectorResult | None = None, raises: Exception | None = None) -> None:
        self._result = result
        self._raises = raises
        self.queries: list[Any] = []

    async def query(self, query: Any) -> ConnectorResult:
        self.queries.append(query)
        if self._raises is not None:
            raise self._raises
        assert self._result is not None
        return self._result


class _FakeRegistry:
    def __init__(self, connector: Any = None, raises: Exception | None = None) -> None:
        self._connector = connector
        self._raises = raises

    async def get(self, tenant_id: UUID, kind: ConnectorKind) -> Any:
        if self._raises is not None:
            raise self._raises
        return self._connector


def _ctx(**overrides: Any) -> InvestigationContext:
    ctx = InvestigationContext(
        tenant=TenantContext(tenant_id=new_id(), name="t", slug="t", api_key_id=new_id()),
        investigation_id=new_id(),
        alert={"alert_name": "HighErrorRate", "labels": {"app": "checkout"}, "severity": "critical"},
        service={"name": "checkout", "label_selector": {"app": "checkout"}},
        dependencies={"upstream": [{"name": "frontend"}], "downstream": [{"name": "db"}]},
        metric_catalog={"metrics": [{"metric_name": "http_requests_total"}, {"metric_name": "go_gc_seconds"}]},
        budget=Budget(),
    )
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


# ---- read-only tools ----


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_alert_details() -> None:
    ctx = _ctx()
    out = await get_alert_details_handler({}, ctx)
    assert out["alert"]["alert_name"] == "HighErrorRate"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_metric_names_filtered() -> None:
    ctx = _ctx()
    assert (await list_metric_names_handler({}, ctx))["total"] == 2
    out = await list_metric_names_handler({"filter": "http"}, ctx)
    assert out["metric_names"] == ["http_requests_total"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_service_dependencies() -> None:
    out = await get_service_dependencies_handler({}, _ctx())
    assert [d["name"] for d in out["upstream"]] == ["frontend"]
    assert [d["name"] for d in out["downstream"]] == ["db"]


# ---- query_prometheus ----


@pytest.mark.unit
@pytest.mark.asyncio
async def test_query_prometheus_success() -> None:
    result = ConnectorResult(
        success=True,
        data={"result": [{"metric": {}, "value": [0, "1"]}], "promql": "rate(...)", "truncated": False},
        series_count=1,
    )
    conn = _FakeConnector(result=result)
    ctx = _ctx(connector_registry=_FakeRegistry(connector=conn))
    out = await query_prometheus_handler(
        {"intent_kind": "rate_over_window", "metric": "http_requests_total", "window_seconds": 300}, ctx
    )
    assert out["series_count"] == 1
    assert out["promql"] == "rate(...)"
    assert conn.queries  # the connector was actually queried


@pytest.mark.unit
@pytest.mark.asyncio
async def test_query_prometheus_no_connector() -> None:
    out = await query_prometheus_handler({"intent_kind": "rate_over_window"}, _ctx())
    assert out["error"] == "no_connector"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_query_prometheus_invalid_intent() -> None:
    ctx = _ctx(connector_registry=_FakeRegistry(connector=_FakeConnector()))
    out = await query_prometheus_handler({"intent_kind": "nonsense"}, ctx)
    assert out["error"] == "invalid_intent"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_query_prometheus_no_prometheus_integration() -> None:
    ctx = _ctx(connector_registry=_FakeRegistry(raises=IntegrationNotFound("none")))
    out = await query_prometheus_handler({"intent_kind": "rate_over_window", "metric": "m"}, ctx)
    assert out["error"] == "no_prometheus"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_query_prometheus_query_error() -> None:
    conn = _FakeConnector(raises=ConnectorTimeout("slow"))
    ctx = _ctx(connector_registry=_FakeRegistry(connector=conn))
    out = await query_prometheus_handler({"intent_kind": "rate_over_window", "metric": "m"}, ctx)
    assert out["error"] == "query_failed"
