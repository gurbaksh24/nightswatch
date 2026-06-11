"""KnowledgeService.

Handles:
    * Ingesting runbooks, postmortems, and past investigations.
    * Chunking, embedding, and indexing them in pgvector.
    * Serving tenant-scoped vector search to LLM tools and the API.

Chunking strategy:
    * Markdown / text: heading-aware chunker; ~500 tokens, 50 overlap; preserve
      heading path as chunk metadata.
    * PDF: text is extracted upstream (api layer), then the same chunker runs in
      fixed-token (non-heading) mode.

The embedding provider is pluggable (`EmbeddingProvider` ABC). The repository is
tenant-scoped, so no tenant argument is threaded through.

See LLD §13.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from ai_sre.core.knowledge.chunker import Chunker
from ai_sre.core.knowledge.embedding import EmbeddingProvider
from ai_sre.core.knowledge.repository import KnowledgeRepository
from ai_sre.models.knowledge import KnowledgeChunk, KnowledgeDoc
from ai_sre.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class KnowledgeDocInput:
    title: str
    kind: str  # "runbook" | "postmortem" | "past_investigation"
    text: str
    is_markdown: bool = True
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class KnowledgeSearchResult:
    doc_id: UUID
    chunk_id: UUID
    title: str
    text: str
    headings: list[str]
    score: float  # cosine similarity in [0, 1]; higher is closer


class KnowledgeService:
    """Public surface used by tools and API routes."""

    def __init__(
        self,
        repo: KnowledgeRepository,
        embedder: EmbeddingProvider,
        chunker: Chunker | None = None,
    ) -> None:
        self.repo = repo
        self.embedder = embedder
        self.chunker = chunker or Chunker()

    async def ingest(self, doc: KnowledgeDocInput) -> KnowledgeDoc:
        """Persist the doc, then chunk + embed + persist its chunks."""
        row = await self.repo.create_doc(
            kind=doc.kind,
            title=doc.title,
            metadata=doc.metadata,
        )
        chunks = self.chunker.chunk(doc.text, is_markdown=doc.is_markdown)
        if chunks:
            vectors = await self.embedder.embed_documents([c.text for c in chunks])
            await self.repo.add_chunks(
                [
                    KnowledgeChunk(
                        tenant_id=self.repo.tenant_id,
                        doc_id=row.id,
                        ord=c.ord,
                        text=c.text,
                        headings=c.headings,
                        embedding=vec,
                        tokens=c.tokens,
                    )
                    for c, vec in zip(chunks, vectors, strict=True)
                ]
            )
        logger.info(
            "knowledge.ingested",
            tenant_id=str(self.repo.tenant_id),
            doc_id=str(row.id),
            kind=doc.kind,
            chunks=len(chunks),
        )
        return row

    async def search(
        self,
        query: str,
        k: int = 5,
        *,
        kinds: Sequence[str] | None = None,
    ) -> list[KnowledgeSearchResult]:
        """Tenant-scoped vector search. Returns ≤ k results (never errors on few).

        ``kinds`` restricts to docs of those kinds — the LLM tools use it to
        split runbooks from past incidents.
        """
        query = query.strip()
        if not query:
            return []
        embedding = await self.embedder.embed_query(query)
        hits = await self.repo.search_chunks(embedding, k, kinds=kinds)
        return [
            KnowledgeSearchResult(
                doc_id=chunk.doc_id,
                chunk_id=chunk.id,
                title=title,
                text=chunk.text,
                headings=list(chunk.headings or []),
                score=1.0 - distance,
            )
            for chunk, title, distance in hits
        ]

    async def ingest_past_investigation(
        self, *, investigation_id: UUID, headline: str, body: str
    ) -> KnowledgeDoc:
        """Embed a completed investigation's RCA as a future "past incident" (FR-7.3)."""
        return await self.ingest(
            KnowledgeDocInput(
                title=headline or f"Investigation {investigation_id}",
                kind="past_investigation",
                text=body,
                is_markdown=True,
                metadata={"investigation_id": str(investigation_id)},
            )
        )
