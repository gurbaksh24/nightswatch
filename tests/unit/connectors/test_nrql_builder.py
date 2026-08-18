"""Unit tests for the intent→NRQL builder (spec 0019)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ai_sre.connectors.base import (
    Aggregation,
    ChangeOverTime,
    Percentile,
    RateOverWindow,
    RawNRQL,
    TraceQuery,
)
from ai_sre.connectors.newrelic import queries
from ai_sre.exceptions import ConnectorError, ConnectorUnsupported

pytestmark = pytest.mark.unit


def test_rate_over_window() -> None:
    nrql = queries.build(
        RateOverWindow(metric="http.server.errors", labels={"service": "checkout"})
    )
    assert nrql == (
        "SELECT rate(sum(`http.server.errors`), 1 minute) FROM Metric"
        " WHERE `service` = 'checkout' SINCE 300 seconds ago"
    )


def test_percentile_and_sorted_labels() -> None:
    nrql = queries.build(Percentile(p=0.99, metric="duration", labels={"env": "prd", "app": "x"}))
    assert "percentile(`duration`, 99)" in nrql
    # Sorted keys: app before env.
    assert "WHERE `app` = 'x' AND `env` = 'prd'" in nrql


def test_change_over_time_is_delta_approximation() -> None:
    nrql = queries.build(ChangeOverTime(metric="restarts", window_seconds=900))
    assert nrql == "SELECT max(`restarts`) - min(`restarts`) FROM Metric SINCE 900 seconds ago"


def test_aggregation_with_facet() -> None:
    nrql = queries.build(
        Aggregation(
            op="sum",
            by=("pod", "env"),
            inner=RateOverWindow(metric="errors", labels={"service": "checkout"}),
        )
    )
    assert nrql.startswith("SELECT rate(sum(`errors`), 1 minute) FROM Metric")
    assert nrql.endswith("FACET `pod`, `env`")


def test_aggregation_rejects_unknown_op_and_bad_inner() -> None:
    with pytest.raises(ConnectorError):
        queries.build(Aggregation(op="stddev", inner=RateOverWindow(metric="m")))
    with pytest.raises(ConnectorUnsupported):
        queries.build(Aggregation(op="sum", inner=None))


def test_explicit_bounds_win_over_window() -> None:
    start = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
    end = datetime(2026, 8, 18, 11, 0, tzinfo=UTC)
    nrql = queries.build(RateOverWindow(metric="m"), start=start, end=end)
    assert f"SINCE {int(start.timestamp())} UNTIL {int(end.timestamp())}" in nrql
    assert "seconds ago" not in nrql


def test_string_escaping() -> None:
    nrql = queries.build(RateOverWindow(metric="m", labels={"svc": "a'b\\c"}))
    assert "`svc` = 'a\\'b\\\\c'" in nrql


def test_raw_nrql_passthrough_and_validation() -> None:
    assert queries.build(RawNRQL(query="SELECT count(*) FROM Log")) == ("SELECT count(*) FROM Log")
    for bad in ("", "SELECT 1; SELECT 2", "DELETE FROM x", "x" * 5000):
        with pytest.raises(ConnectorError):
            queries.build(RawNRQL(query=bad))


def test_raw_nrql_keeps_authors_since() -> None:
    start = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
    nrql = queries.build(RawNRQL(query="SELECT count(*) FROM Log SINCE 2 hours ago"), start=start)
    assert nrql.count("SINCE") == 1


def test_unknown_intent_rejected() -> None:
    with pytest.raises(ConnectorUnsupported):
        queries.build(TraceQuery(trace_id="abc"))  # type: ignore[arg-type]


def test_probe_and_discovery_queries_scope_by_selector() -> None:
    probe = queries.selector_probe_query({"service": "checkout"})
    assert probe.startswith("SELECT uniqueCount(metricName) FROM Metric WHERE")
    disco = queries.metric_discovery_query({}, limit=500)
    assert disco == "SELECT uniques(metricName, 500) FROM Metric SINCE 1 day ago"
