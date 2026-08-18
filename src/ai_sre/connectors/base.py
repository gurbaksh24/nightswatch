"""Connector ABC and the typed query union.

`Connector` is the contract every telemetry adapter implements. The
investigation orchestrator depends on this interface, not on any concrete
implementation.

`ConnectorQuery` is a tagged union of typed query intents. Each variant is a
small dataclass — easy for the LLM to fill via tool_use, easy for the
connector to translate to its native query language.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

# ---- Enums and small value types ----


class ConnectorKind(StrEnum):
    PROMETHEUS = "prometheus"
    NEWRELIC = "newrelic"
    # Future:
    # LOKI = "loki"
    # DATADOG = "datadog"
    # SPLUNK = "splunk"
    # OTEL = "otel"


# Kinds that can serve as a tenant's metrics backend, in preference order.
# Discovery and selector validation try these left to right (spec 0019:
# a tenant may have both Prometheus and New Relic connected at once).
METRICS_CONNECTOR_KINDS: tuple[ConnectorKind, ...] = (
    ConnectorKind.PROMETHEUS,
    ConnectorKind.NEWRELIC,
)


@dataclass(frozen=True)
class ConnectorHealth:
    """Outcome of a connector health probe."""

    healthy: bool
    latency_ms: int | None = None
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


# ---- Query intents (tagged union) ----


@dataclass(frozen=True)
class RateOverWindow:
    """`rate(metric{labels}[window])` semantics. Window in seconds."""

    kind: str = field(default="rate_over_window", init=False)
    metric: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    window_seconds: int = 300


@dataclass(frozen=True)
class Aggregation:
    """Aggregate (sum/avg/max/min) of an inner intent, optionally `by` labels."""

    kind: str = field(default="aggregation", init=False)
    op: str = "sum"  # sum | avg | max | min | count
    by: tuple[str, ...] = ()
    inner: Any | None = None  # another QueryIntent


@dataclass(frozen=True)
class Percentile:
    """`histogram_quantile(p, ...)` over a histogram metric."""

    kind: str = field(default="percentile", init=False)
    p: float = 0.95
    metric: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    window_seconds: int = 300


@dataclass(frozen=True)
class ChangeOverTime:
    """`delta(metric[window])` semantics — useful for counters of events."""

    kind: str = field(default="change_over_time", init=False)
    metric: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    window_seconds: int = 900


@dataclass(frozen=True)
class RawPromQL:
    """Escape hatch. The connector parses it and rejects unsafe constructs."""

    kind: str = field(default="raw_promql", init=False)
    query: str = ""


@dataclass(frozen=True)
class RawNRQL:
    """Raw-NRQL escape hatch (spec 0019). Validated by the New Relic connector."""

    kind: str = field(default="raw_nrql", init=False)
    query: str = ""


QueryIntent = RateOverWindow | Aggregation | Percentile | ChangeOverTime | RawPromQL


@dataclass(frozen=True)
class PromQLQuery:
    """Wrapper carrying time bounds + intent for `Connector.query`."""

    kind: str = field(default="promql", init=False)
    intent: QueryIntent | None = None
    start: datetime | None = None
    end: datetime | None = None
    step: timedelta = timedelta(seconds=30)


@dataclass(frozen=True)
class NRQLQuery:
    """Time bounds + intent for the New Relic connector (spec 0019).

    Carries the same generic intents as ``PromQLQuery`` (translated to NRQL
    by the connector) or a ``RawNRQL`` escape hatch.
    """

    kind: str = field(default="nrql", init=False)
    intent: QueryIntent | RawNRQL | None = None
    start: datetime | None = None
    end: datetime | None = None


# Other query types — stubbed for future connectors.
@dataclass(frozen=True)
class LogQuery:
    kind: str = field(default="log", init=False)
    query: str = ""
    start: datetime | None = None
    end: datetime | None = None
    limit: int = 100


@dataclass(frozen=True)
class TraceQuery:
    kind: str = field(default="trace", init=False)
    trace_id: str = ""


@dataclass(frozen=True)
class EventQuery:
    kind: str = field(default="event", init=False)
    service: str = ""
    window_seconds: int = 3600


ConnectorQuery = PromQLQuery | NRQLQuery | LogQuery | TraceQuery | EventQuery


# ---- Result ----


@dataclass(frozen=True)
class ConnectorResult:
    """Common envelope for all connector outputs."""

    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    series_count: int = 0
    point_count: int = 0
    latency_ms: int = 0
    error: str | None = None


# ---- The ABC ----


class Connector(ABC):
    """The single contract every telemetry adapter implements."""

    kind: ConnectorKind

    @abstractmethod
    async def health_check(self) -> ConnectorHealth: ...

    @abstractmethod
    async def discover_topology(self, service: dict[str, Any]) -> dict[str, Any]:
        """Return upstream/downstream services for the given subject service.

        Implemented in spec 0005.
        """

    @abstractmethod
    async def discover_metrics(self, service: dict[str, Any]) -> list[dict[str, Any]]:
        """Return metric catalog entries (name, type, labels) for the service.

        Implemented in spec 0005.
        """

    @abstractmethod
    async def query(self, query: ConnectorQuery) -> ConnectorResult:
        """Execute a typed query.

        Raises:
            ConnectorUnsupported: for query variants this connector can't run
                (e.g. ``LogQuery`` against a Prometheus connector).
            ConnectorTimeout: if the upstream system doesn't respond within the
                configured timeout.
        """

    async def probe_selector(self, selector: dict[str, str]) -> ConnectorResult:
        """Cheap existence probe: is anything matching ``selector`` emitting
        telemetry? ``series_count`` in the result carries the match count.

        Non-abstract so existing connectors/fakes are unaffected (spec 0019);
        connectors that support service-selector validation override it.
        Used by ``core/`` so selector validation never imports a concrete
        connector.
        """
        from ai_sre.exceptions import ConnectorUnsupported

        raise ConnectorUnsupported(f"{type(self).__name__} does not support selector probing.")
