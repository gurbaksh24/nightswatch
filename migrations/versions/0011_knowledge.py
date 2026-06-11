"""knowledge_doc + knowledge_chunk tables + HNSW vector index

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-11

Spec: specs/0014-knowledge-ingestion.md

Notes:
    * The knowledge tables were modelled in the ORM (models/knowledge.py) but
      never migrated — this migration creates them.
    * Embeddings are 384-dim (BGE-small / hashing fallback). The HNSW index
      uses cosine ops to match the search operator (`<=>`). Searches always
      filter by tenant_id first, then order by cosine distance.
    * Requires the pgvector extension; created here if absent.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMBEDDING_DIM = 384


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "knowledge_doc",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=1024), nullable=False),
        sa.Column("source_object_key", sa.String(length=512), nullable=True),
        sa.Column(
            "extra_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_knowledge_doc_tenant_id", "knowledge_doc", ["tenant_id"])

    op.create_table(
        "knowledge_chunk",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "doc_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_doc.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "headings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("embedding", Vector(_EMBEDDING_DIM), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_knowledge_chunk_tenant_id", "knowledge_chunk", ["tenant_id"])
    op.create_index("ix_knowledge_chunk_doc_id", "knowledge_chunk", ["doc_id"])
    # HNSW index for approximate nearest-neighbour over cosine distance.
    op.execute(
        "CREATE INDEX ix_knowledge_chunk_embedding_hnsw "
        "ON knowledge_chunk USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunk_embedding_hnsw", table_name="knowledge_chunk")
    op.drop_index("ix_knowledge_chunk_doc_id", table_name="knowledge_chunk")
    op.drop_index("ix_knowledge_chunk_tenant_id", table_name="knowledge_chunk")
    op.drop_table("knowledge_chunk")
    op.drop_index("ix_knowledge_doc_tenant_id", table_name="knowledge_doc")
    op.drop_table("knowledge_doc")
