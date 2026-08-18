"""Unit tests for NewRelicConnector against a mocked NerdGraph (spec 0019)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from ai_sre.connectors.base import LogQuery, NRQLQuery, RawNRQL
from ai_sre.connectors.newrelic.connector import NewRelicConfig, NewRelicConnector
from ai_sre.exceptions import ConnectorTimeout, ConnectorUnsupported

pytestmark = pytest.mark.unit

_ENDPOINT = "https://api.newrelic.com/graphql"


def _connector(max_series: int = 10_000) -> NewRelicConnector:
    return NewRelicConnector(
        NewRelicConfig(account_id=1234, api_key="NRAK-test", max_series=max_series)
    )


def _nrql_response(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data": {"actor": {"account": {"nrql": {"results": results}}}}}


@pytest.mark.asyncio
@respx.mock
async def test_query_success_parses_results() -> None:
    route = respx.post(_ENDPOINT).mock(
        return_value=httpx.Response(200, json=_nrql_response([{"count": 42}]))
    )
    conn = _connector()
    result = await conn.query(NRQLQuery(intent=RawNRQL(query="SELECT count(*) FROM Log")))
    assert result.success
    assert result.series_count == 1
    assert result.data["results"] == [{"count": 42}]
    assert result.data["nrql"] == "SELECT count(*) FROM Log"
    # API key travels in the header, never the URL.
    assert route.calls.last.request.headers["API-Key"] == "NRAK-test"
    await conn.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_graphql_errors_become_failed_result() -> None:
    respx.post(_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"errors": [{"message": "NRQL Syntax Error"}]})
    )
    conn = _connector()
    result = await conn.query(NRQLQuery(intent=RawNRQL(query="SELECT broken FROM")))
    assert not result.success
    assert "NRQL Syntax Error" in (result.error or "")
    await conn.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_http_error_becomes_failed_result_and_timeout_raises() -> None:
    respx.post(_ENDPOINT).mock(return_value=httpx.Response(500, text="boom"))
    conn = _connector()
    result = await conn.query(NRQLQuery(intent=RawNRQL(query="SELECT 1 FROM Log")))
    assert not result.success and "500" in (result.error or "")

    respx.post(_ENDPOINT).mock(side_effect=httpx.ConnectTimeout("slow"))
    with pytest.raises(ConnectorTimeout):
        await conn.query(NRQLQuery(intent=RawNRQL(query="SELECT 1 FROM Log")))
    await conn.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_results_truncated_at_max_series() -> None:
    rows = [{"v": i} for i in range(10)]
    respx.post(_ENDPOINT).mock(return_value=httpx.Response(200, json=_nrql_response(rows)))
    conn = _connector(max_series=3)
    result = await conn.query(NRQLQuery(intent=RawNRQL(query="SELECT v FROM Metric")))
    assert result.success and result.series_count == 3 and result.data["truncated"]
    await conn.aclose()


@pytest.mark.asyncio
async def test_non_nrql_query_unsupported() -> None:
    conn = _connector()
    with pytest.raises(ConnectorUnsupported):
        await conn.query(LogQuery(query="x"))
    await conn.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_health_check_paths() -> None:
    respx.post(_ENDPOINT).mock(
        return_value=httpx.Response(
            200, json={"data": {"actor": {"account": {"id": 1234, "name": "Acme"}}}}
        )
    )
    conn = _connector()
    health = await conn.health_check()
    assert health.healthy and health.latency_ms is not None

    # Key can't see the account → unhealthy, not an exception.
    respx.post(_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"data": {"actor": {"account": None}}})
    )
    health = await conn.health_check()
    assert not health.healthy and health.details["kind"] == "account_not_found"
    await conn.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_discover_metrics_parses_uniques() -> None:
    respx.post(_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json=_nrql_response([{"uniques.metricName": ["a.count", "b.duration"]}]),
        )
    )
    conn = _connector()
    entries = await conn.discover_metrics({"label_selector": {"service": "checkout"}})
    assert [e["metric_name"] for e in entries] == ["a.count", "b.duration"]
    assert all(e["labels"] == {} for e in entries)
    await conn.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_probe_selector_extracts_count() -> None:
    respx.post(_ENDPOINT).mock(
        return_value=httpx.Response(200, json=_nrql_response([{"uniqueCount.metricName": 7}]))
    )
    conn = _connector()
    result = await conn.probe_selector({"service": "checkout"})
    assert result.success and result.series_count == 7

    respx.post(_ENDPOINT).mock(
        return_value=httpx.Response(200, json=_nrql_response([{"uniqueCount.metricName": 0}]))
    )
    result = await conn.probe_selector({"service": "ghost"})
    assert result.success and result.series_count == 0
    await conn.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_discover_topology_maps_calls_edges() -> None:
    guid = "MYGUID"
    entity_search = {
        "data": {
            "actor": {
                "entitySearch": {"results": {"entities": [{"guid": guid, "name": "checkout"}]}}
            }
        }
    }
    related = {
        "data": {
            "actor": {
                "entity": {
                    "relatedEntities": {
                        "results": [
                            {
                                "type": "CALLS",
                                "source": {"entity": {"guid": guid, "name": "checkout"}},
                                "target": {"entity": {"guid": "g2", "name": "payments"}},
                            },
                            {
                                "type": "CALLS",
                                "source": {"entity": {"guid": "g3", "name": "gateway"}},
                                "target": {"entity": {"guid": guid, "name": "checkout"}},
                            },
                            {
                                "type": "HOSTS",
                                "source": {"entity": {"guid": "g4", "name": "host-1"}},
                                "target": {"entity": {"guid": guid, "name": "checkout"}},
                            },
                        ]
                    }
                }
            }
        }
    }
    calls = iter([entity_search, related])
    respx.post(_ENDPOINT).mock(side_effect=lambda _req: httpx.Response(200, json=next(calls)))
    conn = _connector()
    topo = await conn.discover_topology({"name": "checkout", "label_selector": {}})
    assert topo["downstream"] == [{"name": "payments", "via_label": "nerdgraph"}]
    assert topo["upstream"] == [{"name": "gateway", "via_label": "nerdgraph"}]
    await conn.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_discover_topology_degrades_to_empty_on_surprise() -> None:
    respx.post(_ENDPOINT).mock(return_value=httpx.Response(200, json={"data": {}}))
    conn = _connector()
    topo = await conn.discover_topology({"name": "checkout", "label_selector": {}})
    assert topo == {"upstream": [], "downstream": []}
    await conn.aclose()
