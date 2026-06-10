"""Unit tests for PromQlBuilder + the safe-PromQL validator (spec 0009)."""

from __future__ import annotations

import pytest

from ai_sre.connectors.base import (
    Aggregation,
    ChangeOverTime,
    Percentile,
    RateOverWindow,
    RawPromQL,
)
from ai_sre.connectors.prometheus.queries import PromQlBuilder
from ai_sre.exceptions import ConnectorError

builder = PromQlBuilder()


@pytest.mark.unit
def test_build_rate_over_window() -> None:
    out = builder.build(
        RateOverWindow(metric="http_requests_total", labels={"app": "checkout"}, window_seconds=300)
    )
    assert out == 'rate(http_requests_total{app="checkout"}[300s])'


@pytest.mark.unit
def test_build_percentile() -> None:
    out = builder.build(Percentile(p=0.95, metric="http_request_duration_seconds_bucket"))
    assert out.startswith("histogram_quantile(0.95,")
    assert "by (le)" in out


@pytest.mark.unit
def test_build_change_over_time() -> None:
    out = builder.build(ChangeOverTime(metric="errors_total", window_seconds=900))
    assert out == "delta(errors_total[900s])"


@pytest.mark.unit
def test_build_aggregation_wraps_inner() -> None:
    out = builder.build(
        Aggregation(op="sum", by=("code",), inner=RateOverWindow(metric="http_requests_total"))
    )
    assert out.startswith("sum by (code)(")
    assert "rate(http_requests_total" in out


@pytest.mark.unit
def test_build_raw_promql_safe_passes() -> None:
    assert builder.build(RawPromQL(query='up{job="api"}')) == 'up{job="api"}'


# ---- validate_raw rejections ----


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    [
        '{job="api"}',                      # bare selector at start
        'rate({job="api"}[5m])',            # bare selector after (
        'sum by (x) ({__name__="y"})',      # bare selector after (
        'topk(foo, http_requests_total)',   # topk without integer bound
        'ALERTS{alertstate="firing"}',      # forbidden token
        'count_values("v", up)',            # cardinality bomb
        '',                                 # empty
    ],
)
def test_validate_raw_rejects(bad: str) -> None:
    with pytest.raises(ConnectorError):
        builder.validate_raw(bad)


@pytest.mark.unit
@pytest.mark.parametrize(
    "ok",
    [
        'up{job="api"}',
        'rate(http_requests_total{app="checkout"}[5m])',
        'sum(rate(http_requests_total[5m]))',
        'topk(5, rate(http_requests_total[5m]))',   # bounded topk is fine
        'histogram_quantile(0.95, sum(rate(d_bucket[5m])) by (le))',
    ],
)
def test_validate_raw_accepts(ok: str) -> None:
    assert builder.validate_raw(ok) == ok
