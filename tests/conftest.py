"""Shared test fixtures.

Most tests do not need the DB. Tests that DO declare `pytest.mark.integration`
and request the `db_session` fixture.

The LLM is always faked via `FakeLLMProvider`; no test ever hits a real
provider. Same for Prometheus, Slack, etc.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
import pytest_asyncio


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def fake_llm():
    """Yield a FakeLLMProvider. Tests pre-load it with scripted responses."""
    from tests.fakes.llm import FakeLLMProvider
    yield FakeLLMProvider()
