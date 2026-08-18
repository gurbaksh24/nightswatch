"""FastAPI application entrypoint.

Composes the API routers, middleware, and lifecycle hooks. Module wiring
(repositories, services, registries) is set up here via FastAPI dependencies
and the `lifespan` context.

If you need to add a new route, add a new file under `api/` and include
its router below — do not bloat this file.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select

from ai_sre.api import (
    alerts,
    delivery,
    feedback,
    integrations,
    investigations,
    knowledge,
    services,
    tenants,
)
from ai_sre.config import get_settings
from ai_sre.db import dispose_engine, get_engine
from ai_sre.middleware.rate_limit import (
    SlidingWindowRateLimiter,
    WebhookRateLimitMiddleware,
)
from ai_sre.observability.metrics import QUEUE_DEPTH, render_latest
from ai_sre.utils.logging import configure_logging, get_logger

logger = get_logger(__name__)


def _instrument_otel_runtime() -> None:
    """Wire OTel auto-instrumentation for SQLAlchemy + httpx (spec 0017).

    Exporter configuration is deployer-owned via the standard ``OTEL_*`` env
    vars; without a configured SDK the produced spans are no-ops. Telemetry
    must never block startup, so each instrumentor is best-effort.
    """
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception as exc:  # pragma: no cover - depends on installed extras
        logger.warning("otel.httpx_instrumentation_skipped", error=str(exc))
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument(engine=get_engine().sync_engine)
    except Exception as exc:  # pragma: no cover - depends on installed extras
        logger.warning("otel.sqlalchemy_instrumentation_skipped", error=str(exc))


def _instrument_otel_app(app: FastAPI) -> None:
    """Per-app FastAPI instrumentation (one span per request, NFR-7.1)."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception as exc:  # pragma: no cover - depends on installed extras
        logger.warning("otel.fastapi_instrumentation_skipped", error=str(exc))


async def _refresh_queue_depth() -> None:
    """Best-effort refresh of ``ai_sre_queue_depth`` from Procrastinate.

    Requires the Procrastinate app to be open (lifespan); silently skips
    otherwise so /metrics always answers.
    """
    try:
        from ai_sre.workers.app import procrastinate_app

        queues = await procrastinate_app.job_manager.list_queues_async()
        for q in queues:
            QUEUE_DEPTH.labels(queue=q["name"]).set(q.get("todo", 0))
    except Exception as exc:
        logger.debug("metrics.queue_depth_unavailable", error=str(exc))


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application startup/shutdown.

    Initialise: logging, OTel instrumentation, Procrastinate app. Tear down
    in reverse order and dispose the DB engine.

    The API process needs the Procrastinate app open so it can `enqueue(...)`
    from request handlers (via `JobQueue`). It does NOT run a worker — that's
    a separate process (`workers/investigation_worker.py`).
    """
    settings = get_settings()
    configure_logging(settings)
    _instrument_otel_runtime()
    # Pre-warm the embedding model so the first knowledge upload/search doesn't
    # pay the (potentially multi-second) model-load cost. Best-effort.
    from ai_sre.api.deps import get_embedder

    try:
        await get_embedder().warm_up()
    except Exception as exc:  # pragma: no cover - never block startup on this
        logger.warning("embedding.warmup_failed", error=str(exc))

    from ai_sre.workers.app import procrastinate_app

    async with procrastinate_app.open_async():
        yield
    # Graceful shutdown: the Procrastinate app closed above; release the DB
    # pool so in-flight connections drain instead of being severed.
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AI-SRE",
        version="0.0.1",
        debug=settings.debug,
        lifespan=lifespan,
    )

    # Per-tenant webhook rate limiting (spec 0017). 0 disables.
    if settings.rate_limit_webhook_per_minute > 0:
        app.add_middleware(
            WebhookRateLimitMiddleware,
            limiter=SlidingWindowRateLimiter(limit=settings.rate_limit_webhook_per_minute),
        )

    # Routers
    app.include_router(tenants.router, prefix="/v1", tags=["tenants"])
    app.include_router(integrations.router, prefix="/v1", tags=["integrations"])
    app.include_router(services.router, prefix="/v1", tags=["services"])
    app.include_router(alerts.router, prefix="/v1", tags=["alerts"])
    app.include_router(investigations.router, prefix="/v1", tags=["investigations"])
    app.include_router(feedback.router, prefix="/v1", tags=["feedback"])
    app.include_router(delivery.router, prefix="/v1", tags=["delivery"])
    app.include_router(knowledge.router, prefix="/v1", tags=["knowledge"])

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> JSONResponse:
        """Readiness: the DB answers a trivial query and the queue's schema
        is reachable. 503 with per-check detail otherwise (spec 0017)."""
        checks: dict[str, str] = {}
        try:
            async with get_engine().connect() as conn:
                await conn.execute(select(1))
            checks["db"] = "ok"
        except Exception as exc:
            checks["db"] = f"error: {type(exc).__name__}"
        try:
            from ai_sre.workers.app import procrastinate_app

            if await procrastinate_app.job_manager.check_connection_async():
                checks["queue"] = "ok"
            else:
                checks["queue"] = "error: schema_missing"
        except Exception as exc:
            checks["queue"] = f"error: {type(exc).__name__}"

        ready = all(v == "ok" for v in checks.values())
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ok" if ready else "unavailable", "checks": checks},
        )

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        """Prometheus exposition (NFR-7.2). Queue depth is refreshed
        best-effort on each scrape."""
        await _refresh_queue_depth()
        body, content_type = render_latest()
        return Response(content=body, media_type=content_type)

    _instrument_otel_app(app)
    return app


app = create_app()
