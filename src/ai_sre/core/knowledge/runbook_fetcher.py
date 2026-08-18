"""Guarded fetch of runbook URLs from alert annotations (spec 0021).

The URL originates in a *webhook payload*, so this module treats it as
hostile input (SSRF surface):

    * ``https://`` only.
    * Every resolved address must be public — private/loopback/link-local/
      reserved/multicast ranges are rejected. (DNS-rebinding TOCTOU is
      acknowledged and out of MVP scope.)
    * Redirects are not followed (they could bounce to an internal host).
    * Size cap + timeout + content-type allowlist.

Extraction: markdown/plain pass through (heading-aware chunking); HTML is
reduced to text with the stdlib parser; PDF goes through pypdf. Anything
else is rejected.
"""

from __future__ import annotations

import asyncio
import io
import ipaddress
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from ai_sre.exceptions import AISREError
from ai_sre.utils.logging import get_logger

logger = get_logger(__name__)

# getaddrinfo-compatible: (host, port) -> sequence of addrinfo tuples.
Resolver = Callable[..., Sequence[tuple[Any, ...]]]

_MARKDOWN_TYPES = frozenset({"text/markdown", "text/x-markdown"})
_SKIPPED_HTML_TAGS = frozenset({"script", "style", "noscript"})


class RunbookFetchError(AISREError):
    """The runbook URL was rejected or could not be fetched/parsed."""

    code = "knowledge.runbook_fetch_failed"


@dataclass(frozen=True)
class FetchedRunbook:
    """Extracted text ready for the chunker."""

    text: str
    is_markdown: bool
    content_type: str


class _TextExtractor(HTMLParser):
    """Minimal HTML → text: visible text only, block-ish newlines."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_HTML_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_HTML_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._parts.append(data.strip())

    def text(self) -> str:
        return "\n".join(self._parts)


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text()


def _pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader  # heavy import, deferred

    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


async def assert_url_safe(url: str, *, resolver: Resolver = socket.getaddrinfo) -> None:
    """Reject non-https URLs and hosts that resolve to non-public addresses.

    ``resolver`` is injectable so tests never touch real DNS.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise RunbookFetchError(f"Runbook URL must be https, got {parsed.scheme!r}.")
    host = parsed.hostname
    if not host:
        raise RunbookFetchError("Runbook URL has no hostname.")
    try:
        infos = await asyncio.to_thread(resolver, host, 443)
    except socket.gaierror as exc:
        raise RunbookFetchError(f"Runbook host does not resolve: {host}") from exc
    if not infos:
        raise RunbookFetchError(f"Runbook host does not resolve: {host}")
    for info in infos:
        address = str(info[4][0])
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise RunbookFetchError(f"Unparseable resolved address: {address}") from exc
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise RunbookFetchError(f"Runbook host resolves to a non-public address ({address}).")


async def fetch_runbook(
    url: str,
    *,
    timeout_seconds: int,
    max_bytes: int,
    resolver: Resolver = socket.getaddrinfo,
) -> FetchedRunbook:
    """Fetch + extract one runbook. Raises :class:`RunbookFetchError`."""
    await assert_url_safe(url, resolver=resolver)

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        raise RunbookFetchError(f"Runbook fetch failed: {exc}") from exc

    if resp.status_code != 200:
        raise RunbookFetchError(f"Runbook fetch returned HTTP {resp.status_code}.")
    if len(resp.content) > max_bytes:
        raise RunbookFetchError(
            f"Runbook larger than the {max_bytes}-byte cap ({len(resp.content)})."
        )

    content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type == "application/pdf":
        text = _pdf_to_text(resp.content)
        is_markdown = False
    elif content_type == "text/html":
        text = _html_to_text(resp.text)
        is_markdown = False
    elif content_type in _MARKDOWN_TYPES or content_type.startswith("text/"):
        text = resp.text
        is_markdown = content_type in _MARKDOWN_TYPES or url.lower().endswith((".md", ".markdown"))
    else:
        raise RunbookFetchError(f"Unsupported runbook content-type: {content_type!r}.")

    if not text.strip():
        raise RunbookFetchError("Runbook fetched but contained no extractable text.")
    return FetchedRunbook(text=text, is_markdown=is_markdown, content_type=content_type)
