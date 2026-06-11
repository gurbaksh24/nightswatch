"""Heading-aware Markdown chunker (spec 0014).

Splits a document into chunks suitable for embedding:

    * **Markdown / plain text** — heading-aware. ATX headings (``#``..``######``)
      become chunk boundaries; each chunk records the heading *path* it lives
      under (e.g. ``["Runbook", "Rollback", "Steps"]``). A section longer than
      ``target_tokens`` is further split into overlapping windows so no single
      chunk blows past the embedder's context.
    * **PDF / opaque text** — pass ``is_markdown=False`` for a pure fixed-token
      windowing with overlap (no heading detection).

Token counting is pluggable (``count_tokens``). The default is a cheap, fully
deterministic char-based heuristic (~4 chars/token) so the chunker has no model
or network dependency; a ``tiktoken``-backed counter can be injected later
without touching callers.

This module lives in ``core`` and stays dependency-free on purpose — heavy libs
(embedders) are injected, not imported here.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_PARA_SPLIT_RE = re.compile(r"\n\s*\n")


def default_count_tokens(text: str) -> int:
    """Deterministic ~4-chars-per-token estimate (no external deps).

    Good enough for chunk-size budgeting; swap for a tiktoken-backed counter
    via the ``count_tokens`` arg when exact accounting is needed.
    """
    return max(1, len(text) // 4)


@dataclass(frozen=True)
class Chunk:
    """One chunk ready to embed + persist as a ``knowledge_chunk`` row."""

    ord: int
    text: str
    tokens: int
    headings: list[str] = field(default_factory=list)


class Chunker:
    """Split documents into embedding-sized chunks."""

    def __init__(
        self,
        *,
        target_tokens: int = 500,
        overlap_tokens: int = 50,
        count_tokens: Callable[[str], int] = default_count_tokens,
    ) -> None:
        if overlap_tokens >= target_tokens:
            raise ValueError("overlap_tokens must be smaller than target_tokens")
        self.target_tokens = target_tokens
        self.overlap_tokens = overlap_tokens
        self._count = count_tokens

    def chunk(self, text: str, *, is_markdown: bool = True) -> list[Chunk]:
        """Return ordered chunks for ``text`` (empty list if blank)."""
        sections = (
            self._split_sections(text)
            if is_markdown
            else [([], text)]
        )
        chunks: list[Chunk] = []
        for headings, body in sections:
            body = body.strip()
            if not body:
                continue
            for piece in self._split_body(body):
                chunks.append(
                    Chunk(
                        ord=len(chunks),
                        text=piece,
                        tokens=self._count(piece),
                        headings=list(headings),
                    )
                )
        return chunks

    # ---- Markdown sectioning ----

    def _split_sections(self, text: str) -> list[tuple[list[str], str]]:
        """Walk lines, tracking the heading stack; emit (heading_path, body)."""
        sections: list[tuple[list[str], str]] = []
        stack: list[tuple[int, str]] = []  # (level, title)
        buf: list[str] = []

        def flush() -> None:
            if buf and "\n".join(buf).strip():
                sections.append(([t for _, t in stack], "\n".join(buf)))

        for line in text.splitlines():
            m = _HEADING_RE.match(line)
            if m:
                # The buffer so far belongs to the *current* path — flush before
                # we mutate the stack for the new heading.
                flush()
                buf = []
                level = len(m.group(1))
                title = m.group(2).strip()
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, title))
            else:
                buf.append(line)
        flush()
        return sections

    # ---- Body windowing ----

    def _split_body(self, body: str) -> list[str]:
        if self._count(body) <= self.target_tokens:
            return [body]

        units: list[str] = []
        for para in _PARA_SPLIT_RE.split(body):
            para = para.strip()
            if not para:
                continue
            if self._count(para) <= self.target_tokens:
                units.append(para)
            else:
                units.extend(self._split_by_words(para))

        chunks: list[str] = []
        cur: list[str] = []
        cur_tokens = 0
        for unit in units:
            ut = self._count(unit)
            if cur and cur_tokens + ut > self.target_tokens:
                chunks.append("\n\n".join(cur))
                cur = self._overlap_tail(cur)
                cur_tokens = sum(self._count(x) for x in cur)
            cur.append(unit)
            cur_tokens += ut
        if cur:
            chunks.append("\n\n".join(cur))
        return chunks

    def _split_by_words(self, para: str) -> list[str]:
        """Window an oversized paragraph into target-sized word runs w/ overlap."""
        words = para.split()
        pieces: list[str] = []
        cur: list[str] = []
        for word in words:
            cur.append(word)
            if self._count(" ".join(cur)) >= self.target_tokens:
                pieces.append(" ".join(cur))
                cur = self._overlap_tail_words(cur)
        if cur:
            pieces.append(" ".join(cur))
        return pieces

    def _overlap_tail(self, units: list[str]) -> list[str]:
        """Trailing units whose combined tokens stay within the overlap budget."""
        tail: list[str] = []
        total = 0
        for unit in reversed(units):
            t = self._count(unit)
            if tail and total + t > self.overlap_tokens:
                break
            tail.insert(0, unit)
            total += t
            if total >= self.overlap_tokens:
                break
        return tail

    def _overlap_tail_words(self, words: list[str]) -> list[str]:
        tail: list[str] = []
        for word in reversed(words):
            tail.insert(0, word)
            if self._count(" ".join(tail)) >= self.overlap_tokens:
                break
        return tail
