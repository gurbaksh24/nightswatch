"""tool_call table

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-09

Spec: specs/0008-llm-gateway.md

Notes:
    * The ToolDispatcher persists one `tool_call` row per LLM tool invocation
      (FR-5.3). The table was modelled in ORM but never migrated — the spec's
      "Migration: none" note was wrong.
    * FKs to both `investigation` and `investigation_stage` (which exists from
      0007), so a tool call is attributable to the stage that made it.
    * `report` is still deferred to spec 0012.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_call",
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
            "stage_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("investigation_stage.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("output_object_key", sa.String(length=512), nullable=True),
        sa.Column(
            "latency_ms", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("outcome", sa.String(length=32), nullable=False),
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
    )
    op.create_index("ix_tool_call_tenant_id", "tool_call", ["tenant_id"])
    op.create_index("ix_tool_call_tool_name", "tool_call", ["tool_name"])
    op.create_index(
        "ix_tool_call_investigation_created",
        "tool_call",
        ["investigation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_tool_call_investigation_created", table_name="tool_call")
    op.drop_index("ix_tool_call_tool_name", table_name="tool_call")
    op.drop_index("ix_tool_call_tenant_id", table_name="tool_call")
    op.drop_table("tool_call")
