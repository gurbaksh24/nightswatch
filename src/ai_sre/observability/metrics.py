"""Central ``prometheus_client`` Counter/Histogram/Gauge singletons (spec 0017).

Names and labels follow docs/03-lld.md §16. Everything is registered on the
default global registry and exposed by the API's ``/metrics`` endpoint
(NFR-7.2). Instrumented code imports the singleton and calls
``.labels(...).inc()/.observe()`` — no other layer talks to
``prometheus_client`` directly.

Label-cardinality note: ``tenant_id`` is a deliberate label on the webhook and
rate-limit counters (per LLD §16); MVP scale is ≤100 tenants (NFR-2.1), well
within Prometheus comfort.

Worker processes share this module but don't serve ``/metrics`` in MVP — their
series live in the worker process and are exported only if a worker-side
exporter is added later. Stage/tool/LLM metrics below are therefore
best-effort in the API process (backtests and tests still exercise them).
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

INVESTIGATION_DURATION_SECONDS = Histogram(
    "ai_sre_investigation_duration_seconds",
    "Wall-clock duration of one investigation stage (label `stage`; "
    '`stage="total"` for the whole pipeline).',
    labelnames=("stage",),
    # Stages have 30-90s timeouts and the pipeline a 300s wall budget.
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 90, 150, 300),
)

TOOL_CALLS_TOTAL = Counter(
    "ai_sre_tool_calls_total",
    "LLM tool dispatches, by tool name and outcome (success|error).",
    labelnames=("tool", "outcome"),
)

LLM_TOKENS_TOTAL = Counter(
    "ai_sre_llm_tokens_total",
    "Tokens consumed by LLM calls (input + output).",
    labelnames=("provider", "model"),
)

LLM_COST_USD_TOTAL = Counter(
    "ai_sre_llm_cost_usd_total",
    "Estimated USD spend on LLM calls.",
    labelnames=("provider", "model"),
)

WEBHOOK_RECEIVED_TOTAL = Counter(
    "ai_sre_webhook_received_total",
    "Alertmanager webhook requests received (pre-verification).",
    labelnames=("tenant_id",),
)

DELIVERY_TOTAL = Counter(
    "ai_sre_delivery_total",
    "Report delivery attempts, by channel and outcome (success|error).",
    labelnames=("channel", "outcome"),
)

QUEUE_DEPTH = Gauge(
    "ai_sre_queue_depth",
    "Jobs waiting (`todo`) per Procrastinate queue. Refreshed best-effort on each /metrics scrape.",
    labelnames=("queue",),
)

RATE_LIMIT_HITS_TOTAL = Counter(
    "ai_sre_rate_limit_hits_total",
    "Requests rejected by the per-tenant rate limiter.",
    labelnames=("tenant_id", "scope"),
)


def render_latest() -> tuple[bytes, str]:
    """Render the default registry in Prometheus text exposition format.

    Returns ``(body, content_type)`` for the ``/metrics`` route.
    """
    return generate_latest(), CONTENT_TYPE_LATEST
