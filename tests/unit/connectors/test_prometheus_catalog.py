"""Unit tests for metric-catalog + topology discovery (spec 0005).

Drives the real :class:`MetricCatalogDiscovery` / :class:`TopologyDiscovery`
against a respx-mocked Prometheus, so the connector's discovery HTTP
primitives (``/api/v1/series``, ``/api/v1/metadata``) are exercised too.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from ai_sre.connectors.prometheus.catalog import (
    MetricCatalogDiscovery,
    TopologyConventions,
    TopologyDiscovery,
    selector_to_matcher,
)
from ai_sre.connectors.prometheus.connector import (
    PrometheusAuth,
    PrometheusConfig,
    PrometheusConnector,
)

BASE_URL = "http://prom.example.com"


def _make(*, max_series: int = 10_000) -> PrometheusConnector:
    config = PrometheusConfig(
        base_url=BASE_URL,
        auth=PrometheusAuth(kind="none"),
        query_timeout_seconds=10,
        max_points=10_000,
        max_series=max_series,
    )
    client = httpx.AsyncClient(base_url=BASE_URL, timeout=10)
    return PrometheusConnector(config, client=client)


def _series(data: list[dict[str, str]]) -> dict[str, Any]:
    return {"status": "success", "data": data}


def _metadata(data: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    return {"status": "success", "data": data}


# ---- selector_to_matcher ----


@pytest.mark.unit
def test_selector_to_matcher_sorts_and_escapes() -> None:
    assert selector_to_matcher({"b": "2", "a": "1"}) == '{a="1",b="2"}'
    assert selector_to_matcher({"x": 'a"b'}) == '{x="a\\"b"}'


# ---- MetricCatalogDiscovery ----


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_catalog_groups_series_by_metric_and_enriches() -> None:
    respx.get(f"{BASE_URL}/api/v1/series").mock(
        return_value=httpx.Response(
            200,
            json=_series(
                [
                    {"__name__": "http_requests_total", "app": "checkout", "method": "GET"},
                    {"__name__": "http_requests_total", "app": "checkout", "method": "POST"},
                    {"__name__": "process_cpu_seconds_total", "app": "checkout"},
                ]
            ),
        )
    )
    respx.get(f"{BASE_URL}/api/v1/metadata").mock(
        return_value=httpx.Response(
            200,
            json=_metadata(
                {
                    "http_requests_total": [
                        {"type": "counter", "help": "Total HTTP requests", "unit": ""}
                    ]
                }
            ),
        )
    )

    entries = await MetricCatalogDiscovery(_make()).discover({"app": "checkout"})

    # Sorted by name; one entry per distinct __name__.
    assert [e.name for e in entries] == [
        "http_requests_total",
        "process_cpu_seconds_total",
    ]
    http = entries[0]
    assert http.type == "counter"
    assert http.help_text == "Total HTTP requests"
    assert http.labels["method"] == ["GET", "POST"]  # sorted sample
    assert http.truncated is False
    # No metadata for the cpu metric → type unknown, unit inferred from suffix.
    cpu = entries[1]
    assert cpu.type == "unknown"
    assert cpu.unit == "seconds"


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_catalog_truncates_high_cardinality_metric() -> None:
    # 1001 series for one metric → exceeds the per-metric truncation cap.
    big = [{"__name__": "big_metric", "i": str(i)} for i in range(1001)]
    respx.get(f"{BASE_URL}/api/v1/series").mock(
        return_value=httpx.Response(200, json=_series(big))
    )
    respx.get(f"{BASE_URL}/api/v1/metadata").mock(
        return_value=httpx.Response(200, json=_metadata({}))
    )

    entries = await MetricCatalogDiscovery(_make()).discover({"app": "checkout"})

    assert len(entries) == 1
    entry = entries[0]
    assert entry.truncated is True
    # Sample values per label are bounded.
    assert len(entry.labels["i"]) <= 20
    # The persisted shape carries the __truncated__ marker (spec 0005).
    assert entry.to_catalog_dict()["labels"]["__truncated__"] is True


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_catalog_empty_selector_returns_nothing_without_calling_prom() -> None:
    route = respx.get(f"{BASE_URL}/api/v1/series").mock(
        return_value=httpx.Response(200, json=_series([]))
    )
    entries = await MetricCatalogDiscovery(_make()).discover({})
    assert entries == []
    assert not route.called


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_catalog_survives_metadata_failure() -> None:
    respx.get(f"{BASE_URL}/api/v1/series").mock(
        return_value=httpx.Response(
            200, json=_series([{"__name__": "up", "app": "checkout"}])
        )
    )
    # Metadata endpoint 500s — discovery should still return the metric.
    respx.get(f"{BASE_URL}/api/v1/metadata").mock(
        return_value=httpx.Response(500, text="boom")
    )
    entries = await MetricCatalogDiscovery(_make()).discover({"app": "checkout"})
    assert [e.name for e in entries] == ["up"]
    assert entries[0].type == "unknown"


# ---- TopologyDiscovery ----


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_topology_infers_edges_and_dedupes() -> None:
    respx.get(f"{BASE_URL}/api/v1/series").mock(
        return_value=httpx.Response(
            200,
            json=_series(
                [
                    {"__name__": "requests_total", "app": "checkout", "dst": "payments"},
                    {"__name__": "requests_total", "app": "checkout", "dst": "payments"},
                    {"__name__": "requests_total", "app": "checkout", "upstream": "frontend"},
                ]
            ),
        )
    )
    deps = await TopologyDiscovery(_make()).discover({"app": "checkout"})

    edges = {(d.direction, d.name) for d in deps}
    assert edges == {("downstream", "payments"), ("upstream", "frontend")}
    # Deduped: the repeated dst=payments collapses to one edge.
    assert len(deps) == 2


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_topology_excludes_self_reference() -> None:
    respx.get(f"{BASE_URL}/api/v1/series").mock(
        return_value=httpx.Response(
            200,
            json=_series(
                [
                    # dst == own selector value → not a real dependency.
                    {"__name__": "x", "app": "checkout", "dst": "checkout"},
                    {"__name__": "x", "app": "checkout", "dst": "payments"},
                ]
            ),
        )
    )
    deps = await TopologyDiscovery(_make()).discover({"app": "checkout"})
    assert {(d.direction, d.name) for d in deps} == {("downstream", "payments")}


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_topology_respects_custom_conventions() -> None:
    respx.get(f"{BASE_URL}/api/v1/series").mock(
        return_value=httpx.Response(
            200,
            json=_series([{"__name__": "x", "app": "checkout", "peer": "cache"}]),
        )
    )
    conventions = TopologyConventions(downstream_labels=("peer",))
    deps = await TopologyDiscovery(_make(), conventions).discover({"app": "checkout"})
    assert {(d.direction, d.name) for d in deps} == {("downstream", "cache")}


# ---- ABC delegation (connector.discover_*) ----


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_connector_discover_topology_returns_grouped_dict() -> None:
    respx.get(f"{BASE_URL}/api/v1/series").mock(
        return_value=httpx.Response(
            200,
            json=_series(
                [
                    {"__name__": "x", "app": "checkout", "dst": "payments"},
                    {"__name__": "x", "app": "checkout", "upstream": "frontend"},
                ]
            ),
        )
    )
    result = await _make().discover_topology({"label_selector": {"app": "checkout"}})
    assert [e["name"] for e in result["downstream"]] == ["payments"]
    assert [e["name"] for e in result["upstream"]] == ["frontend"]


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_connector_discover_metrics_returns_catalog_dicts() -> None:
    respx.get(f"{BASE_URL}/api/v1/series").mock(
        return_value=httpx.Response(
            200, json=_series([{"__name__": "up", "app": "checkout"}])
        )
    )
    respx.get(f"{BASE_URL}/api/v1/metadata").mock(
        return_value=httpx.Response(200, json=_metadata({}))
    )
    result = await _make().discover_metrics({"label_selector": {"app": "checkout"}})
    assert result == [
        {
            "metric_name": "up",
            "metric_type": "unknown",
            "labels": {"app": ["checkout"]},
            "unit": None,
            "help_text": None,
        }
    ]
