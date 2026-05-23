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

from fastapi import FastAPI

from ai_sre.api import (
    alerts,
    feedback,
    integrations,
    investigations,
    services,
    tenants,
)
from ai_sre.config import get_settings
from ai_sre.utils.logging import configure_logging


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application startup/shutdown.

    Initialise: logging, DB pool, Procrastinate app, connector/delivery/llm
    registries, OTel. Tear down in reverse order.

    The API process needs the Procrastinate app open so it can `enqueue(...)`
    from request handlers (via `JobQueue`). It does NOT run a worker — that's
    a separate process (`workers/investigation_worker.py`).
    """
    settings = get_settings()
    configure_logging(settings)
    # TODO: init DB pool, registries, OTel.
    from ai_sre.workers.app import procrastinate_app
    async with procrastinate_app.open_async():
        yield
    # TODO: graceful shutdown.


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AI-SRE",
        version="0.0.1",
        debug=settings.debug,
        lifespan=lifespan,
    )

    # Routers
    app.include_router(tenants.router, prefix="/v1", tags=["tenants"])
    app.include_router(integrations.router, prefix="/v1", tags=["integrations"])
    app.include_router(services.router, prefix="/v1", tags=["services"])
    app.include_router(alerts.router, prefix="/v1", tags=["alerts"])
    app.include_router(investigations.router, prefix="/v1", tags=["investigations"])
    app.include_router(feedback.router, prefix="/v1", tags=["feedback"])

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> dict[str, str]:
        # TODO: check DB, queue, secrets manager
        return {"status": "ok"}

    return app


app = create_app()
