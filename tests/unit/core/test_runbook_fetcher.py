"""Unit tests for the SSRF-guarded runbook fetcher (spec 0021).

No test touches real DNS or the network: the resolver is injected and HTTP
is mocked with respx.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from ai_sre.core.knowledge.runbook_fetcher import (
    RunbookFetchError,
    assert_url_safe,
    fetch_runbook,
)

pytestmark = pytest.mark.unit

_URL = "https://runbooks.example/checkout.md"


def _public_resolver(*_args: Any, **_kwargs: Any) -> list[tuple[Any, ...]]:
    return [(2, 1, 6, "", ("93.184.216.34", 443))]


def _resolver_for(ip: str) -> Any:
    def _resolve(*_args: Any, **_kwargs: Any) -> list[tuple[Any, ...]]:
        return [(2, 1, 6, "", (ip, 443))]

    return _resolve


# ---- URL guards ----


@pytest.mark.asyncio
async def test_rejects_non_https() -> None:
    with pytest.raises(RunbookFetchError, match="https"):
        await assert_url_safe("http://runbooks.example/x", resolver=_public_resolver)
    with pytest.raises(RunbookFetchError):
        await assert_url_safe("file:///etc/passwd", resolver=_public_resolver)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",  # loopback
        "10.1.2.3",  # private
        "192.168.1.1",  # private
        "169.254.169.254",  # link-local (cloud metadata)
        "0.0.0.0",  # unspecified
        "::1",  # v6 loopback
    ],
)
async def test_rejects_non_public_addresses(ip: str) -> None:
    with pytest.raises(RunbookFetchError, match="non-public"):
        await assert_url_safe("https://runbooks.example/x", resolver=_resolver_for(ip))


@pytest.mark.asyncio
async def test_accepts_public_address() -> None:
    await assert_url_safe(_URL, resolver=_public_resolver)  # no raise


# ---- fetch + extraction ----


@pytest.mark.asyncio
@respx.mock
async def test_markdown_passthrough() -> None:
    respx.get(_URL).mock(
        return_value=httpx.Response(
            200,
            text="# Checkout runbook\n\nRestart the pods.",
            headers={"content-type": "text/markdown; charset=utf-8"},
        )
    )
    fetched = await fetch_runbook(
        _URL, timeout_seconds=5, max_bytes=1024, resolver=_public_resolver
    )
    assert fetched.is_markdown and "Restart the pods." in fetched.text


@pytest.mark.asyncio
@respx.mock
async def test_html_reduced_to_text() -> None:
    url = "https://runbooks.example/page"
    respx.get(url).mock(
        return_value=httpx.Response(
            200,
            text="<html><script>evil()</script><h1>Checkout</h1><p>Fix it.</p></html>",
            headers={"content-type": "text/html"},
        )
    )
    fetched = await fetch_runbook(url, timeout_seconds=5, max_bytes=1024, resolver=_public_resolver)
    assert not fetched.is_markdown
    assert "Checkout" in fetched.text and "Fix it." in fetched.text
    assert "evil" not in fetched.text


@pytest.mark.asyncio
@respx.mock
async def test_rejects_oversize_bad_status_and_unknown_type() -> None:
    respx.get(_URL).mock(
        return_value=httpx.Response(200, text="x" * 100, headers={"content-type": "text/plain"})
    )
    with pytest.raises(RunbookFetchError, match="cap"):
        await fetch_runbook(_URL, timeout_seconds=5, max_bytes=10, resolver=_public_resolver)

    respx.get(_URL).mock(return_value=httpx.Response(404))
    with pytest.raises(RunbookFetchError, match="404"):
        await fetch_runbook(_URL, timeout_seconds=5, max_bytes=1024, resolver=_public_resolver)

    respx.get(_URL).mock(
        return_value=httpx.Response(
            200, content=b"\x00", headers={"content-type": "application/octet-stream"}
        )
    )
    with pytest.raises(RunbookFetchError, match="content-type"):
        await fetch_runbook(_URL, timeout_seconds=5, max_bytes=1024, resolver=_public_resolver)


@pytest.mark.asyncio
@respx.mock
async def test_timeout_becomes_fetch_error() -> None:
    respx.get(_URL).mock(side_effect=httpx.ConnectTimeout("slow"))
    with pytest.raises(RunbookFetchError, match="fetch failed"):
        await fetch_runbook(_URL, timeout_seconds=1, max_bytes=1024, resolver=_public_resolver)
