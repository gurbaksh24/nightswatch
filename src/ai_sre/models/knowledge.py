"""KnowledgeDoc + KnowledgeChunk.

Embeddings are stored on KnowledgeChunk via pgvector. HNSW index defined
in the Alembic migration (not at the ORM layer).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sre.db import Base
from ai_sre.models.base import IdMixin, TenantOwnedMixin, TimestampMixin

EMBEDDING_DIM = 384


class KnowledgeDoc(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_doc"

    kind: Mapped[str] = mapped_column(String(32), nullable=False)         # runbook|postmortem|past_investigation
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    extra_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeChunk(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_chunk"

    doc_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("knowledge_doc.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    headings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
