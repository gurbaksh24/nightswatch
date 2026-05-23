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
    # Future:
    # LOKI = "loki"
    # DATADOG = "datadog"
    # SPLUNK = "splunk"
    # OTEL = "otel"


@dataclass(frozen=True)
class ConnectorHealth:
    healthy: bool
    latency_ms: int | None = None
    error: str | None = None


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
    op: str = "sum"                                # sum | avg | max | min | count
    by: tuple[str, ...] = ()
    inner: Any | None = None                       # another QueryIntent


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


QueryIntent = RateOverWindow | Aggregation | Percentile | ChangeOverTime | RawPromQL


@dataclass(frozen=True)
class PromQLQuery:
    """Wrapper carrying time bounds + intent for `Connector.query`."""

    kind: str = field(default="promql", init=False)
    intent: QueryIntent | None = None
    start: datetime | None = None
    end: datetime | None = None
    step: timedelta = timedelta(seconds=30)


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


ConnectorQuery = PromQLQuery | LogQuery | TraceQuery | EventQuery


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
    async def discover_topology(self, service: dict) -> dict:
        """Return upstream/downstream services for the given subject service."""

    @abstractmethod
    async def discover_metrics(self, service: dict) -> list[dict]:
        """Return metric catalog entries (name, type, labels) for the service."""

    @abstractmethod
    async def query(self, query: ConnectorQuery) -> ConnectorResult:
        """Execute a typed query. Raise ConnectorUnsupported for variants the
        connector doesn't handle.
        """
