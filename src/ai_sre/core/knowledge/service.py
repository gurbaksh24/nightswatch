"""KnowledgeService.

Handles:
    * Ingesting runbooks, postmortems, and past investigations.
    * Chunking, embedding, and indexing them in pgvector.
    * Serving tenant-scoped vector search to LLM tools.

Chunking strategy:
    * Markdown / text: heading-aware chunker; ~500 tokens, 50 overlap; preserve
      heading path as chunk metadata.
    * PDF: extract text first, then same chunker.

Embedding provider is pluggable (`EmbeddingProvider` ABC). Default: BGE small
(~33M params) for cost. Hosted alternatives available if the tenant opts in.

See LLD §13.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from ai_sre.api.deps import TenantContext


@dataclass(frozen=True)
class KnowledgeDocInput:
    title: str
    kind: str         # "runbook" | "postmortem" | "past_investigation"
    text: str
    metadata: dict | None = None


@dataclass(frozen=True)
class KnowledgeChunk:
    doc_id: UUID
    text: str
    headings: list[str]
    score: float


class KnowledgeService:
    """Public surface used by tools and API routes."""

    async def ingest(
        self, tenant: TenantContext, doc: KnowledgeDocInput
    ) -> UUID:
        """Persist + chunk + embed. Returns the new doc_id."""
        # TODO(spec-NNNN: knowledge-ingestion)
        raise NotImplementedError

    async def search(
        self, tenant: TenantContext, query: str, k: int = 5
    ) -> list[KnowledgeChunk]:
        """Vector search over the tenant's chunks."""
        # TODO(spec-NNNN: knowledge-search)
        raise NotImplementedError

    async def ingest_past_investigation(self, investigation_id: UUID) -> None:
        """After a successful investigation, embed its report for future RAG."""
        # TODO
        raise NotImplementedError
