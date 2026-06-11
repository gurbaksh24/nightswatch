"""KnowledgeRepository — tenant-scoped CRUD + vector search.

Owns both ``knowledge_doc`` and ``knowledge_chunk``. Every query is scoped to
the repository's ``tenant_id`` (the base class enforces it on doc queries; the
chunk/search queries add the filter explicitly).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sre.core._base.repository import TenantScopedRepository
from ai_sre.models.knowledge import KnowledgeChunk, KnowledgeDoc


class KnowledgeRepository(TenantScopedRepository[KnowledgeDoc]):
    """CRUD for knowledge docs + chunks, scoped to a single tenant."""

    model = KnowledgeDoc

    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        super().__init__(session, tenant_id)

    # ---- docs ----

    async def create_doc(
        self,
        *,
        kind: str,
        title: str,
        source_object_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> KnowledgeDoc:
        doc = KnowledgeDoc(
            tenant_id=self.tenant_id,
            kind=kind,
            title=title,
            source_object_key=source_object_key,
            extra_metadata=metadata or {},
        )
        self.session.add(doc)
        await self.session.flush()
        await self.session.refresh(doc)
        return doc

    async def get_doc(self, doc_id: UUID) -> KnowledgeDoc | None:
        """Active (not soft-deleted) doc owned by this tenant, or None."""
        stmt = (
            self._scoped(select(KnowledgeDoc))
            .where(KnowledgeDoc.id == doc_id)
            .where(KnowledgeDoc.deleted_at.is_(None))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_docs(self) -> Sequence[KnowledgeDoc]:
        """Active docs for the tenant, newest first."""
        stmt = (
            self._scoped(select(KnowledgeDoc))
            .where(KnowledgeDoc.deleted_at.is_(None))
            .order_by(KnowledgeDoc.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def soft_delete_doc(self, doc_id: UUID) -> bool:
        """Mark a doc deleted. Returns False if it wasn't found/active."""
        doc = await self.get_doc(doc_id)
        if doc is None:
            return False
        doc.deleted_at = datetime.now(UTC)
        await self.session.flush()
        return True

    async def count_chunks(self, doc_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.tenant_id == self.tenant_id)
            .where(KnowledgeChunk.doc_id == doc_id)
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ---- chunks ----

    async def add_chunks(self, chunks: list[KnowledgeChunk]) -> None:
        if not chunks:
            return
        self.session.add_all(chunks)
        await self.session.flush()

    async def search_chunks(
        self,
        embedding: list[float],
        k: int,
        *,
        kinds: Sequence[str] | None = None,
    ) -> list[tuple[KnowledgeChunk, str, float]]:
        """k nearest chunks by cosine distance, scoped + excluding deleted docs.

        ``kinds`` restricts to docs of those kinds (e.g. ``["runbook"]``).
        Returns ``(chunk, doc_title, distance)`` triples ordered closest-first.
        Returns fewer than ``k`` (or none) rather than erroring when the tenant
        has few chunks.
        """
        distance = KnowledgeChunk.embedding.cosine_distance(embedding).label("distance")
        stmt = (
            select(KnowledgeChunk, KnowledgeDoc.title, distance)
            .join(KnowledgeDoc, KnowledgeChunk.doc_id == KnowledgeDoc.id)
            .where(KnowledgeChunk.tenant_id == self.tenant_id)
            .where(KnowledgeDoc.deleted_at.is_(None))
            .where(KnowledgeChunk.embedding.isnot(None))
        )
        if kinds:
            stmt = stmt.where(KnowledgeDoc.kind.in_(list(kinds)))
        stmt = stmt.order_by(distance).limit(k)
        result = await self.session.execute(stmt)
        return [(row[0], str(row[1]), float(row[2])) for row in result.all()]
