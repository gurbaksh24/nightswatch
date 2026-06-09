"""investigation_stage table

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-09

Spec: specs/0007-orchestrator-skeleton.md

Notes:
    * The orchestrator persists one `investigation_stage` row per stage
      execution (triage, context_assembly, hypothesis, validation, report).
      The table was modelled in ORM but never migrated.
    * `tool_call` and `report` are deferred to the specs that write them
      (LLM gateway / report stage).
    * UNIQUE(investigation_id, name, attempt) mirrors the ORM and makes the
      orchestrator's per-stage upsert idempotent on retries.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "investigation_stage",
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
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column(
            "attempt", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.UniqueConstraint(
            "investigation_id", "name", "attempt", name="uq_stage_per_attempt"
        ),
    )
    op.create_index(
        "ix_investigation_stage_tenant_id", "investigation_stage", ["tenant_id"]
    )
    op.create_index(
        "ix_investigation_stage_investigation_id",
        "investigation_stage",
        ["investigation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_investigation_stage_investigation_id", table_name="investigation_stage"
    )
    op.drop_index(
        "ix_investigation_stage_tenant_id", table_name="investigation_stage"
    )
    op.drop_table("investigation_stage")
