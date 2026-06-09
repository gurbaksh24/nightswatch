"""Metric catalog + topology discovery for Prometheus.

Two discovery classes, both driven off a single ``/api/v1/series`` sweep
scoped to the subject service's label selector:

    * :class:`MetricCatalogDiscovery` — groups matching series by
      ``__name__`` to build a catalog of metric names, observed label
      keys/values, and (via ``/api/v1/metadata``) type / help / unit.
    * :class:`TopologyDiscovery` — inspects request/RPC convention labels
      (``upstream``, ``dst``, …) on the same series to infer upstream /
      downstream dependency edges.

Why one ``/api/v1/series`` call instead of the spec's per-metric sampling:
a single scoped series sweep returns the full label set of every matching
series, which gives metric names, label discovery, and topology labels in
one round-trip — fewer calls, and easy to reason about at MVP scale (the
spec's DoD is 100 metrics in 10s).

The LLM uses the resulting catalog via the ``list_metric_names`` tool: it
shouldn't have to *guess* that the connection-pool metric is
``hikaricp_active_connections`` — it can look it up.

Dependency direction: this module is connector-layer. It imports only the
connector + utils, never ``core`` or ``api``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ai_sre.connectors.prometheus.connector import PrometheusConnector

# Per-metric sampling limits (the >1000-combination edge case in spec 0005).
_MAX_SERIES_PER_METRIC = 1000          # beyond this we flag the metric truncated
_MAX_SAMPLE_VALUES_PER_LABEL = 20      # distinct values kept per label key

# Unit suffixes Prometheus naming conventions imply, used when /metadata
# doesn't carry an explicit unit.
_UNIT_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("_seconds_total", "seconds"),
    ("_seconds", "seconds"),
    ("_bytes_total", "bytes"),
    ("_bytes", "bytes"),
    ("_ratio", "ratio"),
    ("_total", ""),
)


# ---- Value types ----


@dataclass
class MetricEntry:
    """A discovered metric and its observed shape.

    ``labels`` maps each observed label key to a bounded sample of its
    values. ``truncated`` is set when the metric had more series than we
    sampled — the catalog row records this as ``labels["__truncated__"]``.
    """

    name: str
    type: str = "unknown"           # counter | gauge | histogram | summary | unknown
    labels: dict[str, list[str]] = field(default_factory=dict)
    unit: str | None = None
    help_text: str | None = None
    truncated: bool = False

    def to_catalog_dict(self) -> dict[str, Any]:
        """The persisted shape consumed by the core catalog service.

        Folds ``truncated`` into the ``labels`` JSONB as ``__truncated__``
        (per spec 0005) so the stored row is self-describing.
        """
        labels: dict[str, Any] = dict(self.labels)
        if self.truncated:
            labels["__truncated__"] = True
        return {
            "metric_name": self.name,
            "metric_type": self.type,
            "labels": labels,
            "unit": self.unit,
            "help_text": self.help_text,
        }


@dataclass(frozen=True)
class TopologyConventions:
    """Label keys that, when present on a series, imply a dependency edge.

    Defaults follow common HTTP/RPC instrumentation conventions. Tenant
    overrides are a documented follow-up (spec 0005).
    """

    upstream_labels: tuple[str, ...] = ("upstream", "client", "source_service")
    downstream_labels: tuple[str, ...] = (
        "dst",
        "target",
        "downstream_service",
        "peer_service",
    )


@dataclass(frozen=True)
class DiscoveredDependency:
    """One inferred dependency edge of the subject service."""

    direction: str                  # "upstream" | "downstream"
    name: str
    via_label: str                  # the convention label it was discovered from


# ---- Helpers ----


def _escape_label_value(value: str) -> str:
    """Escape a label value for a PromQL matcher (mirrors queries._escape)."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def selector_to_matcher(selector: dict[str, str]) -> str:
    """Build a ``{k="v",...}`` series matcher from a label selector.

    Keys are sorted for determinism; values escaped. An empty selector
    yields ``{}`` which Prometheus rejects for ``match[]`` — callers should
    guard against empty selectors (services always carry ≥1 label).
    """
    parts = [
        f'{k}="{_escape_label_value(v)}"' for k, v in sorted(selector.items())
    ]
    return "{" + ",".join(parts) + "}"


def _infer_unit(metric_name: str) -> str | None:
    """Best-effort unit from the metric-name suffix; ``None`` if unknown."""
    for suffix, unit in _UNIT_SUFFIXES:
        if metric_name.endswith(suffix):
            return unit or None
    return None


# ---- Discovery ----


class MetricCatalogDiscovery:
    """Discover the metric catalog for a service's label selector."""

    def __init__(self, connector: PrometheusConnector) -> None:
        self.connector = connector

    async def discover(self, selector: dict[str, str]) -> list[MetricEntry]:
        """Sweep matching series, group by metric, and enrich from metadata.

        Returns one :class:`MetricEntry` per distinct ``__name__``. Never
        raises for an empty result; surfaces connector errors to the caller.
        """
        if not selector:
            return []

        series = await self.connector.get_series([selector_to_matcher(selector)])

        # metric_name -> label_key -> set(values); plus per-metric series count.
        grouped: dict[str, dict[str, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        series_counts: dict[str, int] = defaultdict(int)
        for labelset in series:
            name = labelset.get("__name__")
            if not name:
                continue
            series_counts[name] += 1
            for key, value in labelset.items():
                if key == "__name__":
                    continue
                values = grouped[name][key]
                if len(values) < _MAX_SAMPLE_VALUES_PER_LABEL:
                    values.add(value)

        metadata = await self.connector.get_metadata()

        entries: list[MetricEntry] = []
        for name in sorted(grouped):
            meta = (metadata.get(name) or [{}])[0]
            unit = meta.get("unit") or _infer_unit(name)
            entries.append(
                MetricEntry(
                    name=name,
                    type=meta.get("type", "unknown") or "unknown",
                    labels={
                        key: sorted(vals) for key, vals in grouped[name].items()
                    },
                    unit=unit or None,
                    help_text=meta.get("help") or None,
                    truncated=series_counts[name] > _MAX_SERIES_PER_METRIC,
                )
            )
        return entries


class TopologyDiscovery:
    """Discover upstream/downstream dependency edges from label conventions."""

    def __init__(
        self,
        connector: PrometheusConnector,
        conventions: TopologyConventions | None = None,
    ) -> None:
        self.connector = connector
        self.conventions = conventions or TopologyConventions()

    async def discover(self, selector: dict[str, str]) -> list[DiscoveredDependency]:
        """Sweep matching series and infer dependency edges.

        Dedupes by ``(direction, name)`` — the same neighbour discovered via
        multiple labels collapses to one edge (first convention label wins
        for ``via_label``). Self-references (name equal to one of the
        selector's own label values) are dropped.
        """
        if not selector:
            return []

        series = await self.connector.get_series([selector_to_matcher(selector)])
        own_values = set(selector.values())

        # (direction, name) -> via_label, preserving first-seen.
        seen: dict[tuple[str, str], str] = {}
        directions = (
            ("upstream", self.conventions.upstream_labels),
            ("downstream", self.conventions.downstream_labels),
        )
        for labelset in series:
            for direction, label_keys in directions:
                for key in label_keys:
                    value = labelset.get(key)
                    if not value or value in own_values:
                        continue
                    edge = (direction, value)
                    seen.setdefault(edge, key)

        return [
            DiscoveredDependency(direction=direction, name=name, via_label=via)
            for (direction, name), via in seen.items()
        ]
