"""Unit tests for :class:`PrometheusConnector`.

Uses ``respx`` to mock Prometheus HTTP endpoints — no network access. Covers:

    * ``health_check`` happy path + non-200 + transport error + timeout.
    * Instant ``query`` against ``/api/v1/query``.
    * Range ``query`` against ``/api/v1/query_range`` (when start+end set).
    * Bearer auth header is sent.
    * Basic auth credentials are sent.
    * 401 / 500 / Prometheus-level error → ``ConnectorResult(success=False)``.
    * httpx timeout → ``ConnectorTimeout`` raised.
    * series count > ``max_series`` → response trimmed, ``truncated=True``.
    * Non-Prometheus query (e.g. ``LogQuery``) → ``ConnectorUnsupported``.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from ai_sre.connectors.base import (
    LogQuery,
    PromQLQuery,
    RateOverWindow,
    RawPromQL,
)
from ai_sre.connectors.prometheus.connector import (
    PrometheusAuth,
    PrometheusConfig,
    PrometheusConnector,
)
from ai_sre.exceptions import ConnectorTimeout, ConnectorUnsupported

BASE_URL = "http://prom.example.com"


def _make(
    auth: PrometheusAuth | None = None,
    *,
    max_series: int = 10_000,
    timeout_s: int = 10,
) -> PrometheusConnector:
    """Build a connector with a respx-mountable httpx client."""
    config = PrometheusConfig(
        base_url=BASE_URL,
        auth=auth or PrometheusAuth(kind="none"),
        query_timeout_seconds=timeout_s,
        max_points=10_000,
        max_series=max_series,
    )
    # Hand-build the client so respx can attach. transport=respx.MockTransport
    # is set globally via the @respx.mock decorator.
    client = httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=timeout_s,
        headers={"Authorization": f"Bearer {auth.bearer_token}"}
        if auth and auth.kind == "bearer" and auth.bearer_token
        else {},
        auth=(auth.basic_user, auth.basic_password)
        if auth and auth.kind == "basic" and auth.basic_user and auth.basic_password
        else None,
    )
    return PrometheusConnector(config, client=client)


def _instant_response(result_value: list[float] | None = None) -> dict[str, Any]:
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {"__name__": "up", "job": "api"},
                    "value": result_value or [1700000000, "1"],
                }
            ],
        },
    }


# ---- health_check ----


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_health_check_happy_path() -> None:
    respx.get(f"{BASE_URL}/-/healthy").mock(return_value=httpx.Response(200))
    conn = _make()
    h = await conn.health_check()
    assert h.healthy is True
    assert h.latency_ms is not None and h.latency_ms >= 0
    assert h.error is None


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_health_check_non_200_is_unhealthy() -> None:
    respx.get(f"{BASE_URL}/-/healthy").mock(return_value=httpx.Response(503))
    conn = _make()
    h = await conn.health_check()
    assert h.healthy is False
    assert h.error == "HTTP 503"
    assert h.details["status_code"] == 503


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_health_check_transport_error() -> None:
    respx.get(f"{BASE_URL}/-/healthy").mock(
        side_effect=httpx.ConnectError("dns blew up")
    )
    conn = _make()
    h = await conn.health_check()
    assert h.healthy is False
    assert "dns blew up" in (h.error or "")
    assert h.details["kind"] == "http_error"


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_health_check_timeout() -> None:
    respx.get(f"{BASE_URL}/-/healthy").mock(side_effect=httpx.TimeoutException("slow"))
    conn = _make()
    h = await conn.health_check()
    assert h.healthy is False
    assert h.details["kind"] == "timeout"


# ---- instant query ----


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_instant_query_returns_result() -> None:
    route = respx.get(f"{BASE_URL}/api/v1/query").mock(
        return_value=httpx.Response(200, json=_instant_response())
    )
    conn = _make()
    r = await conn.query(
        PromQLQuery(intent=RateOverWindow(metric="http_requests_total", window_seconds=60))
    )
    assert r.success is True
    assert r.series_count == 1
    assert "rate(http_requests_total" in r.data["promql"]
    assert r.data["truncated"] is False
    # PromQL was URL-encoded into the query param.
    assert route.called
    sent = route.calls.last.request.url.params["query"]
    assert sent.startswith("rate(http_requests_total")


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_raw_promql_passes_through_validator() -> None:
    respx.get(f"{BASE_URL}/api/v1/query").mock(
        return_value=httpx.Response(200, json=_instant_response())
    )
    conn = _make()
    r = await conn.query(
        PromQLQuery(intent=RawPromQL(query='up{job="api"}'))
    )
    assert r.success is True
    assert r.data["promql"] == 'up{job="api"}'


# ---- range query ----


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_range_query_picks_query_range_endpoint() -> None:
    range_response = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"job": "api"},
                    "values": [[1700000000, "1"], [1700000060, "2"]],
                }
            ],
        },
    }
    route = respx.get(f"{BASE_URL}/api/v1/query_range").mock(
        return_value=httpx.Response(200, json=range_response)
    )
    conn = _make()
    now = datetime(2024, 1, 1, tzinfo=UTC)
    r = await conn.query(
        PromQLQuery(
            intent=RateOverWindow(metric="up"),
            start=now,
            end=now + timedelta(minutes=5),
            step=timedelta(seconds=30),
        )
    )
    assert r.success is True
    assert r.series_count == 1
    assert r.point_count == 2
    sent = route.calls.last.request.url.params
    assert "start" in sent
    assert "end" in sent
    assert sent["step"] == "30s"


# ---- auth ----


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_bearer_token_is_sent() -> None:
    route = respx.get(f"{BASE_URL}/api/v1/query").mock(
        return_value=httpx.Response(200, json=_instant_response())
    )
    conn = _make(PrometheusAuth(kind="bearer", bearer_token="secret-123"))
    await conn.query(PromQLQuery(intent=RawPromQL(query="up")))
    assert (
        route.calls.last.request.headers.get("Authorization") == "Bearer secret-123"
    )


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_basic_auth_is_sent() -> None:
    route = respx.get(f"{BASE_URL}/api/v1/query").mock(
        return_value=httpx.Response(200, json=_instant_response())
    )
    conn = _make(
        PrometheusAuth(kind="basic", basic_user="alice", basic_password="hunter2")
    )
    await conn.query(PromQLQuery(intent=RawPromQL(query="up")))
    auth_header = route.calls.last.request.headers.get("Authorization", "")
    assert auth_header.startswith("Basic ")
    decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode()
    assert decoded == "alice:hunter2"


# ---- error paths ----


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_401_is_failure_not_exception() -> None:
    respx.get(f"{BASE_URL}/api/v1/query").mock(
        return_value=httpx.Response(401, text="unauthorized")
    )
    conn = _make()
    r = await conn.query(PromQLQuery(intent=RawPromQL(query="up")))
    assert r.success is False
    assert "401" in (r.error or "")
    assert r.data["status_code"] == 401


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_500_is_failure_not_exception() -> None:
    respx.get(f"{BASE_URL}/api/v1/query").mock(
        return_value=httpx.Response(500, text="boom")
    )
    conn = _make()
    r = await conn.query(PromQLQuery(intent=RawPromQL(query="up")))
    assert r.success is False
    assert "500" in (r.error or "")


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_prometheus_level_error_propagates() -> None:
    """Prometheus returns 200 + `status: error` for things like parse errors."""
    respx.get(f"{BASE_URL}/api/v1/query").mock(
        return_value=httpx.Response(
            200, json={"status": "error", "errorType": "bad_data", "error": "parse fail"}
        )
    )
    conn = _make()
    r = await conn.query(PromQLQuery(intent=RawPromQL(query="up")))
    assert r.success is False
    assert "parse fail" in (r.error or "")
    assert r.data["errorType"] == "bad_data"


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_timeout_raises_connector_timeout() -> None:
    respx.get(f"{BASE_URL}/api/v1/query").mock(
        side_effect=httpx.TimeoutException("slow")
    )
    conn = _make(timeout_s=1)
    with pytest.raises(ConnectorTimeout):
        await conn.query(PromQLQuery(intent=RawPromQL(query="up")))


# ---- truncation ----


@respx.mock
@pytest.mark.unit
@pytest.mark.asyncio
async def test_max_series_truncates_response() -> None:
    big_result = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {"metric": {"i": str(i)}, "value": [1700000000, "1"]}
                for i in range(50)
            ],
        },
    }
    respx.get(f"{BASE_URL}/api/v1/query").mock(
        return_value=httpx.Response(200, json=big_result)
    )
    conn = _make(max_series=10)
    r = await conn.query(PromQLQuery(intent=RawPromQL(query="up")))
    assert r.success is True
    assert r.data["truncated"] is True
    assert len(r.data["result"]) == 10
    assert r.series_count == 10


# ---- query-type rejection ----


@pytest.mark.unit
@pytest.mark.asyncio
async def test_log_query_is_rejected() -> None:
    conn = _make()
    with pytest.raises(ConnectorUnsupported):
        await conn.query(LogQuery(query="error rate"))
