"""report table

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-10

Spec: specs/0012-report-and-read-endpoints.md

Notes:
    * The ReportStage produces one structured RCA per investigation; the
      orchestrator persists it here. Modelled in ORM but never migrated — the
      spec's "table exists" note was wrong; this migration creates it.
    * UNIQUE(investigation_id) — one report per investigation (the orchestrator
      upserts on it).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("investigation.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "schema_version",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'v1'"),
        ),
        sa.Column("headline", sa.String(length=2048), nullable=False),
        sa.Column(
            "confidence",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'low'"),
        ),
        sa.Column(
            "hypotheses",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "next_actions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "related_incidents",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "delivery_receipts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
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
        sa.UniqueConstraint("investigation_id", name="uq_report_per_investigation"),
    )
    op.create_index("ix_report_tenant_id", "report", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_report_tenant_id", table_name="report")
    op.drop_table("report")
