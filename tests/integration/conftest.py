"""Integration-test fixtures.

Integration tests run against a real Postgres. They are SKIPPED automatically
if `AI_SRE_TEST_DB_URL` is not set (or if the DB is unreachable), so the unit
test pass stays green on developer machines without Docker.

Schema lifecycle: a session-scoped fixture creates the schema once via
`Base.metadata.create_all`, and drops it at teardown. Per-test isolation is
provided by truncating the relevant tables — that's enough for the small,
narrowly-scoped specs we're testing.
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


@pytest_asyncio.fixture(scope="session")
async def db_engine() -> AsyncIterator[AsyncEngine]:
    """Engine bound to the integration test DB. Skips the test if unavailable."""
    url = _resolve_db_url()
    if not url:
        pytest.skip("AI_SRE_TEST_DB_URL not set; skipping integration tests.")
    # Point the app at the same database for the duration of the test session.
    os.environ["AI_SRE_DB_URL"] = url

    from ai_sre import db as db_module
    from ai_sre.config import get_settings

    get_settings.cache_clear()
    db_module._engine = None  # type: ignore[attr-defined]
    db_module._sessionmaker = None  # type: ignore[attr-defined]

    engine = create_async_engine(url, future=True)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"Could not initialise test DB: {exc}")
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Yield a per-test session. Tables are truncated between tests."""
    async with db_engine.begin() as conn:
        # Truncate everything the spec 0001 tests touch; cascade picks up FKs.
        await conn.exec_driver_sql(
            "TRUNCATE TABLE api_key, tenant RESTART IDENTITY CASCADE;"
        )
    sm = async_sessionmaker(db_engine, expire_on_commit=False)
    async with sm() as session:
        yield session
        await session.commit()


@pytest_asyncio.fixture
async def client(db_engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    """Yield an httpx AsyncClient bound to the FastAPI app, in-process.

    Truncates per test for isolation. Lifespan is not started — ASGITransport
    skips it by default, so Procrastinate is not opened.
    """
    async with db_engine.begin() as conn:
        await conn.exec_driver_sql(
            "TRUNCATE TABLE api_key, tenant RESTART IDENTITY CASCADE;"
        )
    from ai_sre.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
