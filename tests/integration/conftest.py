"""Integration-test fixtures.

Integration tests run against a real Postgres. They are SKIPPED automatically
if `AI_SRE_TEST_DB_URL` is not set (or if the DB is unreachable), so the unit
test pass stays green on developer machines without Docker.

Scoping note: every async fixture here is **function-scoped**. pytest-asyncio
0.23+ creates a fresh event loop per test by default; asyncpg refuses to
share a connection across loops, so a session-scoped engine would explode on
the second test with ``got Future ... attached to a different loop``. We pay
the per-test cost of `drop_all` + `create_all` (a few hundred ms) in exchange
for keeping every connection on the same loop as the test that uses it.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ai_sre.db import Base


def _resolve_db_url() -> str | None:
    """Return the override DB URL for integration tests, or None to skip."""
    return os.environ.get("AI_SRE_TEST_DB_URL")


def _reset_app_db_singletons() -> None:
    """Force the app's lazy engine + sessionmaker to be rebuilt.

    The app caches the engine and sessionmaker at module level (`db._engine`,
    `db._sessionmaker`). When a previous test built them on a now-closed
    event loop, the next test would try to use them on its own loop and
    asyncpg would refuse. Clearing the cache forces a fresh build on the
    current test's loop.
    """
    from ai_sre import db as db_module
    from ai_sre.config import get_settings

    get_settings.cache_clear()
    db_module._engine = None  # type: ignore[attr-defined]
    db_module._sessionmaker = None  # type: ignore[attr-defined]


@pytest_asyncio.fixture
async def db_engine(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    """Per-test engine bound to the integration test DB.

    Skips the test if `AI_SRE_TEST_DB_URL` is missing or the DB is unreachable.
    Recreates the schema fresh for each test (drop_all + create_all).
    """
    url = _resolve_db_url()
    if not url:
        pytest.skip("AI_SRE_TEST_DB_URL not set; skipping integration tests.")

    # Point the app at the test DB for the duration of this test.
    monkeypatch.setenv("AI_SRE_DB_URL", url)
    _reset_app_db_singletons()

    engine = create_async_engine(url, future=True)
    try:
        async with engine.begin() as conn:
            # The ORM has a real circular FK between `alert` and
            # `investigation` (alert.investigation_id ↔ investigation.
            # triggering_alert_id), so `Base.metadata.drop_all` can't sort
            # tables. Drop the entire schema and recreate — robust against
            # whatever future cycles the data model grows.
            await conn.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE;")
            await conn.exec_driver_sql("CREATE SCHEMA public;")
            # `Base.metadata.create_all` builds the *full* ORM model set,
            # including knowledge_chunk.embedding (pgvector). Make sure the
            # extension is registered before we create that column's type.
            await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector;")
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # pragma: no cover - environment-dependent
        await engine.dispose()
        pytest.skip(f"Could not initialise test DB: {exc}")

    try:
        yield engine
    finally:
        await engine.dispose()
        # Drop the app-level cached engine so the next test doesn't
        # inherit one that is bound to a now-closed loop.
        _reset_app_db_singletons()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Yield a per-test session. Schema is already fresh for this test."""
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    async with sm() as session:
        yield session
        await session.commit()


@pytest_asyncio.fixture
async def client(db_engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    """Yield an httpx AsyncClient bound to the FastAPI app, in-process.

    Lifespan is not started — ASGITransport skips it by default, so the
    Procrastinate app (which is opened in lifespan) is not initialised.
    Spec 0001 routes don't need the queue.
    """
    from ai_sre.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
