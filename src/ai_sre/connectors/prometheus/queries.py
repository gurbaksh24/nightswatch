"""Typed PromQL builders.

The LLM never writes raw PromQL strings directly into our HTTP body. It
chooses a `QueryIntent` variant; the functions here translate it. This is the
seam that prevents PromQL injection (NFR-5.6).

When the LLM legitimately needs flexibility, it can use `RawPromQL`, which
goes through `parse_safe_promql` — a permissive-but-bounded validator.
"""

from __future__ import annotations

import re

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
    # Admin / alert-state endpoints — never legitimate in a diagnosis query.
    "ALERTS{", "alertstate=",
    # `count_values` explodes cardinality by minting a label per distinct value.
    "count_values(",
)

# A `{` that starts a selector with no preceding metric name (start of string,
# or right after `(`, `,`, whitespace, or `[`). These scan *all* series and are
# the main cardinality / injection risk — e.g. `rate({job="x"}[5m])`.
_BARE_SELECTOR = re.compile(r"(?:^|[\s(,\[])\{")

# topk/bottomk without an explicit small integer bound (e.g. `topk by (...)` or
# `topk(some_expr, ...)`): unbounded result sets.
_UNBOUNDED_TOPK = re.compile(r"\b(?:topk|bottomk)\s*\(\s*(?![0-9])")


def parse_safe_promql(q: str) -> str:
    """Validate a raw PromQL expression. Reject obvious red flags.

    A coarse string filter, not a full parser — but enough to make the LLM's
    raw-PromQL escape hatch safe (NFR-5.6). The right long-term answer is the
    prometheus query parser; this is sufficient for MVP. Rejects:

        * empty / over-long queries,
        * admin/alert-state and cardinality-bomb functions,
        * bare ``{...}`` selectors with no metric name (full-series scans),
        * ``topk``/``bottomk`` without a literal integer bound.
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
    if q.startswith("{") or _BARE_SELECTOR.search(q):
        raise ConnectorError(
            "PromQL selectors must be anchored to a metric name (no bare {...})."
        )
    if _UNBOUNDED_TOPK.search(q):
        raise ConnectorError("topk/bottomk requires a literal integer bound.")
    return q


class PromQlBuilder:
    """Object wrapper over the module functions (spec 0009 contract)."""

    def build(self, intent: QueryIntent) -> str:
        """Translate a typed intent into PromQL."""
        return build(intent)

    def validate_raw(self, raw: str) -> str:
        """Validate raw PromQL; returns it unchanged or raises ConnectorError."""
        return parse_safe_promql(raw)
