"""Per-tenant rate limiting for the alert webhook (spec 0017, NFR-1.2).

Deliberate deviation from the spec's ``slowapi`` suggestion, documented here:
slowapi is decorator-oriented and keys on remote IP by default, while this
requirement is a per-tenant fixed budget keyed off the ``{tenant_id}`` URL
segment. The whole need is a ~50-line sliding-window counter, so we implement
it directly rather than adding a dependency and fighting its key extraction.
The contract the spec cares about is preserved:

    * default 100 requests/minute per tenant, configurable
      (``AI_SRE_RATE_LIMIT_WEBHOOK_PER_MINUTE``; ``0`` disables),
    * breach → 429 with a ``Retry-After`` header,
    * every hit is recorded: a structured WARNING log (the durable log
      pipeline is the persistence layer — the spec's files-to-touch lists no
      migration, so no table) plus ``ai_sre_rate_limit_hits_total``.

State is in-process: each API replica enforces its own window (the spec asks
for in-memory). A shared store is a scale-out follow-up.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from ai_sre.observability.metrics import RATE_LIMIT_HITS_TOTAL
from ai_sre.utils.logging import get_logger

logger = get_logger(__name__)

_WEBHOOK_PREFIX = "/v1/webhooks/"


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of one limiter check."""

    allowed: bool
    retry_after_seconds: int = 0


@dataclass
class SlidingWindowRateLimiter:
    """Sliding-window request limiter, keyed by an opaque string.

    Keeps a deque of request timestamps per key; a request is allowed while
    fewer than ``limit`` requests happened in the trailing ``window_seconds``.
    ``clock`` is injectable for deterministic tests. ``limit <= 0`` disables
    the limiter entirely.
    """

    limit: int
    window_seconds: float = 60.0
    clock: Callable[[], float] = time.monotonic
    _events: dict[str, deque[float]] = field(default_factory=dict)

    def check(self, key: str) -> RateLimitDecision:
        """Record + allow the request, or deny it with a retry hint."""
        if self.limit <= 0:
            return RateLimitDecision(allowed=True)
        now = self.clock()
        window = self._events.setdefault(key, deque())
        cutoff = now - self.window_seconds
        while window and window[0] <= cutoff:
            window.popleft()
        if len(window) < self.limit:
            window.append(now)
            return RateLimitDecision(allowed=True)
        # Denied: the request is NOT recorded (a flood of rejected requests
        # must not push the retry horizon out forever).
        retry_after = max(1, int(window[0] + self.window_seconds - now) + 1)
        return RateLimitDecision(allowed=False, retry_after_seconds=retry_after)


class WebhookRateLimitMiddleware:
    """Applies the per-tenant limiter to ``/v1/webhooks/*`` requests only.

    The tenant key is the last path segment (the webhook routes are
    tenant-addressed: ``/v1/webhooks/alertmanager/{tenant_id}``). Every other
    path passes through untouched.
    """

    def __init__(self, app: ASGIApp, limiter: SlidingWindowRateLimiter) -> None:
        self.app = app
        self.limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(_WEBHOOK_PREFIX):
            await self.app(scope, receive, send)
            return

        tenant_key = scope["path"].rstrip("/").rsplit("/", 1)[-1]
        decision = self.limiter.check(tenant_key)
        if decision.allowed:
            await self.app(scope, receive, send)
            return

        # Rare but high-signal: record the hit (structured log + metric).
        logger.warning(
            "rate_limit.hit",
            tenant_id=tenant_key,
            path=scope["path"],
            limit_per_minute=self.limiter.limit,
            retry_after_seconds=decision.retry_after_seconds,
        )
        RATE_LIMIT_HITS_TOTAL.labels(tenant_id=tenant_key, scope="webhook").inc()
        response: Response = JSONResponse(
            status_code=429,
            content={
                "detail": {
                    "code": "rate_limit.exceeded",
                    "message": "Webhook rate limit exceeded for this tenant.",
                }
            },
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )
        await response(scope, receive, send)
