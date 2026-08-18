"""Typed intent → NRQL translation (spec 0019).

The LLM never writes NRQL into an HTTP body directly: it emits the same
generic ``QueryIntent`` variants the Prometheus connector consumes, and this
module renders them as NRQL. That translation seam is the injection guard
(NFR-5.6); the ``raw_nrql`` escape hatch goes through ``validate_raw``.

Deliberate approximations (documented per the spec):

* ``rate_over_window``  → ``SELECT rate(sum(m), 1 minute) FROM Metric ...``
* ``change_over_time``  → ``SELECT max(m) - min(m) FROM Metric ...`` — a
  delta approximation; it undercounts across counter resets, which is
  acceptable for triage-grade signals.
* ``aggregation(op, by, inner)`` → the op applied to the inner intent's
  metric with ``FACET`` for the ``by`` labels (NRQL has no nested selects).
"""

from __future__ import annotations

from datetime import datetime

from ai_sre.connectors.base import (
    Aggregation,
    ChangeOverTime,
    Percentile,
    QueryIntent,
    RateOverWindow,
    RawNRQL,
)
from ai_sre.exceptions import ConnectorError, ConnectorUnsupported

_ALLOWED_OPS = frozenset({"sum", "avg", "max", "min", "count"})
_MAX_RAW_LEN = 4096


def escape_string(value: str) -> str:
    """Escape a value for inclusion in a single-quoted NRQL string."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _ident(name: str) -> str:
    """Backtick-quote an attribute/metric identifier."""
    return "`" + name.replace("`", "") + "`"


def where_clause(labels: dict[str, str]) -> str:
    """Render a label selector as an NRQL WHERE conjunction (sorted keys)."""
    if not labels:
        return ""
    parts = [f"{_ident(k)} = '{escape_string(v)}'" for k, v in sorted(labels.items())]
    return " WHERE " + " AND ".join(parts)


def _since(window_seconds: int, start: datetime | None, end: datetime | None) -> str:
    """Explicit bounds win over the intent's trailing window."""
    if start is not None and end is not None:
        return f" SINCE {int(start.timestamp())} UNTIL {int(end.timestamp())}"
    if start is not None:
        return f" SINCE {int(start.timestamp())}"
    return f" SINCE {window_seconds} seconds ago"


def validate_raw(query: str) -> str:
    """Coarse safety gate for raw NRQL (mirrors ``parse_safe_promql``'s role).

    NRQL is read-only by construction, so this guards against smuggling
    (multiple statements) and unbounded payloads rather than mutation.
    Returns the stripped query; raises :class:`ConnectorError` on rejection.
    """
    stripped = query.strip()
    if not stripped:
        raise ConnectorError("Empty NRQL query.")
    if len(stripped) > _MAX_RAW_LEN:
        raise ConnectorError(f"NRQL query longer than {_MAX_RAW_LEN} chars.")
    if ";" in stripped:
        raise ConnectorError("NRQL must be a single statement (no ';').")
    head = stripped.lstrip().upper()
    if not (head.startswith("SELECT") or head.startswith("FROM")):
        raise ConnectorError("NRQL must start with SELECT or FROM.")
    return stripped


def build(
    intent: QueryIntent | RawNRQL,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> str:
    """Render a typed intent as NRQL. Raises ConnectorError/-Unsupported."""
    match intent:
        case RawNRQL(query=q):
            nrql = validate_raw(q)
            # Only append bounds when the author didn't set their own window.
            if start is not None and "SINCE" not in nrql.upper():
                nrql += _since(0, start, end)
            return nrql
        case RateOverWindow(metric=m, labels=labels, window_seconds=w):
            return (
                f"SELECT rate(sum({_ident(m)}), 1 minute) FROM Metric"
                f"{where_clause(labels)}{_since(w, start, end)}"
            )
        case ChangeOverTime(metric=m, labels=labels, window_seconds=w):
            return (
                f"SELECT max({_ident(m)}) - min({_ident(m)}) FROM Metric"
                f"{where_clause(labels)}{_since(w, start, end)}"
            )
        case Percentile(p=p, metric=m, labels=labels, window_seconds=w):
            return (
                f"SELECT percentile({_ident(m)}, {p * 100:g}) FROM Metric"
                f"{where_clause(labels)}{_since(w, start, end)}"
            )
        case Aggregation(op=op, by=by, inner=inner):
            if op not in _ALLOWED_OPS:
                raise ConnectorError(f"Unsupported aggregation op: {op!r}")
            if not isinstance(inner, RateOverWindow | ChangeOverTime | Percentile):
                raise ConnectorUnsupported(
                    "NRQL aggregation supports rate/change/percentile inners only."
                )
            facet = ""
            if by:
                facet = " FACET " + ", ".join(_ident(b) for b in by)
            m, labels, w = inner.metric, inner.labels, inner.window_seconds
            if isinstance(inner, RateOverWindow):
                select = f"rate({op}({_ident(m)}), 1 minute)"
            elif isinstance(inner, Percentile):
                select = f"percentile({_ident(m)}, {inner.p * 100:g})"
            else:
                select = f"{op}({_ident(m)})"
            return (
                f"SELECT {select} FROM Metric{where_clause(labels)}{_since(w, start, end)}{facet}"
            )
        case _:
            raise ConnectorUnsupported(f"Cannot render intent {intent!r} as NRQL.")


def metric_discovery_query(selector: dict[str, str], *, limit: int = 1000) -> str:
    """Metric-name discovery scoped to the service selector."""
    return (
        f"SELECT uniques(metricName, {limit}) FROM Metric{where_clause(selector)} SINCE 1 day ago"
    )


def selector_probe_query(selector: dict[str, str]) -> str:
    """Existence probe: distinct metric names matching the selector."""
    return f"SELECT uniqueCount(metricName) FROM Metric{where_clause(selector)} SINCE 1 hour ago"
