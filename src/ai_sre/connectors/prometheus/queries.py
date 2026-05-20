"""Typed PromQL builders.

The LLM never writes raw PromQL strings directly into our HTTP body. It
chooses a `QueryIntent` variant; the functions here translate it. This is the
seam that prevents PromQL injection (NFR-5.6).

When the LLM legitimately needs flexibility, it can use `RawPromQL`, which
goes through `parse_safe_promql` — a permissive-but-bounded validator.
"""

from __future__ import annotations

from ai_sre.connectors.base import (
    Aggregation,
    ChangeOverTime,
    Percentile,
    QueryIntent,
    RateOverWindow,
    RawPromQL,
)
from ai_sre.exceptions import ConnectorError


def _labels_selector(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    parts = [f'{k}="{_escape(v)}"' for k, v in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


def _escape(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"')


def build(intent: QueryIntent) -> str:
    """Translate a typed intent into a PromQL string."""
    match intent:
        case RateOverWindow(metric=m, labels=lbl, window_seconds=w):
            return f"rate({m}{_labels_selector(lbl)}[{w}s])"
        case Aggregation(op=op, by=by, inner=inner):
            inner_expr = build(inner) if inner is not None else ""
            by_clause = f" by ({','.join(by)})" if by else ""
            return f"{op}{by_clause}({inner_expr})"
        case Percentile(p=p, metric=m, labels=lbl, window_seconds=w):
            return (
                f"histogram_quantile({p}, sum(rate({m}{_labels_selector(lbl)}[{w}s])) "
                f"by (le))"
            )
        case ChangeOverTime(metric=m, labels=lbl, window_seconds=w):
            return f"delta({m}{_labels_selector(lbl)}[{w}s])"
        case RawPromQL(query=q):
            return parse_safe_promql(q)
        case _:
            raise ConnectorError(f"Unknown intent type: {type(intent).__name__}")


# ---- Safe-PromQL parser ----

_FORBIDDEN_TOKENS = (
    # Write paths and admin endpoints — we never want these in a query.
    "absent_over_time(", "ALERTS{", "alertstate=",
    # Cheap defence against extremely cardinality-heavy operations.
)


def parse_safe_promql(q: str) -> str:
    """Validate a raw PromQL expression. Reject obvious red flags.

    This is a coarse filter, not a full parser. The right long-term answer is
    to use prometheus-go-bindings via grpc, but a string filter is sufficient
    for MVP and adequately restricts the LLM's escape hatch.
    """
    q = q.strip()
    if not q:
        raise ConnectorError("Empty PromQL.")
    if len(q) > 4096:
        raise ConnectorError("PromQL too long.")
    lower = q.lower()
    for tok in _FORBIDDEN_TOKENS:
        if tok.lower() in lower:
            raise ConnectorError(f"PromQL contains forbidden token: {tok!r}")
    # Heuristic: bare `{...}` with no metric name causes huge cardinality scans.
    if q.startswith("{"):
        raise ConnectorError("PromQL must begin with a metric name.")
    return q
