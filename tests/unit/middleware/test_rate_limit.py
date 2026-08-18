"""Unit tests for the per-tenant webhook rate limiter (spec 0017)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ai_sre.middleware.rate_limit import (
    SlidingWindowRateLimiter,
    WebhookRateLimitMiddleware,
)


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ---- limiter core ----


@pytest.mark.unit
def test_allows_up_to_limit_then_denies_at_boundary() -> None:
    clock = _FakeClock()
    limiter = SlidingWindowRateLimiter(limit=3, clock=clock)
    assert all(limiter.check("t1").allowed for _ in range(3))
    denied = limiter.check("t1")
    assert not denied.allowed
    assert denied.retry_after_seconds >= 1


@pytest.mark.unit
def test_window_slides_and_recovers() -> None:
    clock = _FakeClock()
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60, clock=clock)
    assert limiter.check("t1").allowed
    assert limiter.check("t1").allowed
    assert not limiter.check("t1").allowed
    clock.advance(61)  # both events age out of the window
    assert limiter.check("t1").allowed


@pytest.mark.unit
def test_denied_requests_do_not_extend_the_window() -> None:
    clock = _FakeClock()
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60, clock=clock)
    assert limiter.check("t1").allowed
    for _ in range(50):  # a flood of rejected requests must not push recovery out
        assert not limiter.check("t1").allowed
    clock.advance(61)
    assert limiter.check("t1").allowed


@pytest.mark.unit
def test_keys_are_isolated() -> None:
    limiter = SlidingWindowRateLimiter(limit=1, clock=_FakeClock())
    assert limiter.check("tenant-a").allowed
    assert not limiter.check("tenant-a").allowed
    assert limiter.check("tenant-b").allowed


@pytest.mark.unit
def test_zero_limit_disables() -> None:
    limiter = SlidingWindowRateLimiter(limit=0, clock=_FakeClock())
    assert all(limiter.check("t1").allowed for _ in range(500))


# ---- middleware ----


def _app_with_limiter(limit: int) -> FastAPI:
    app = FastAPI()

    @app.post("/v1/webhooks/alertmanager/{tenant_id}")
    async def webhook(tenant_id: str) -> dict[str, str]:
        return {"tenant": tenant_id}

    @app.get("/v1/other")
    async def other() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(WebhookRateLimitMiddleware, limiter=SlidingWindowRateLimiter(limit=limit))
    return app


@pytest.mark.unit
@pytest.mark.asyncio
async def test_webhook_gets_429_with_retry_after() -> None:
    transport = ASGITransport(app=_app_with_limiter(limit=2))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(2):
            assert (await client.post("/v1/webhooks/alertmanager/t-1")).status_code == 200
        resp = await client.post("/v1/webhooks/alertmanager/t-1")
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) >= 1
    assert resp.json()["detail"]["code"] == "rate_limit.exceeded"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_limit_is_per_tenant() -> None:
    transport = ASGITransport(app=_app_with_limiter(limit=1))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.post("/v1/webhooks/alertmanager/t-1")).status_code == 200
        assert (await client.post("/v1/webhooks/alertmanager/t-1")).status_code == 429
        assert (await client.post("/v1/webhooks/alertmanager/t-2")).status_code == 200


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_webhook_paths_bypass_limiter() -> None:
    transport = ASGITransport(app=_app_with_limiter(limit=1))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(5):
            assert (await client.get("/v1/other")).status_code == 200
