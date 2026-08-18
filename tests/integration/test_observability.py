"""Integration tests for spec 0017: /metrics exposition and /readyz checks."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

# Every metric family docs/03-lld.md §16 promises, plus the rate-limit hits
# counter spec 0017 adds. Prometheus emits HELP/TYPE headers for registered
# families even before any labelled child exists.
_EXPECTED_METRICS = (
    "ai_sre_investigation_duration_seconds",
    "ai_sre_tool_calls_total",
    "ai_sre_llm_tokens_total",
    "ai_sre_llm_cost_usd_total",
    "ai_sre_webhook_received_total",
    "ai_sre_delivery_total",
    "ai_sre_queue_depth",
    "ai_sre_rate_limit_hits_total",
)


async def test_metrics_exposes_documented_families(client: AsyncClient) -> None:
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    for name in _EXPECTED_METRICS:
        assert f"# TYPE {name}" in body, f"metric family missing: {name}"


async def test_readyz_reports_db_ok(client: AsyncClient) -> None:
    """The DB check passes against the integration database. The queue check
    is honest about the test harness: lifespan (which opens Procrastinate)
    isn't run under ASGITransport, so overall readiness may be 503 here —
    what matters is per-check reporting."""
    resp = await client.get("/readyz")
    assert resp.status_code in (200, 503)
    payload = resp.json()
    assert payload["checks"]["db"] == "ok"
    assert "queue" in payload["checks"]


async def test_healthz_still_ok(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
