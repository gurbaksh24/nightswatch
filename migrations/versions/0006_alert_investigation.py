"""alert + investigation tables

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-09

Spec: specs/0006-alert-webhook.md

Notes:
    * `alert` and `investigation` were modelled in ORM since the initial
      scaffold but never migrated; alert ingestion (spec 0006) needs them.
    * They have a circular FK: alert.investigation_id -> investigation.id and
      investigation.triggering_alert_id -> alert.id. We create `alert` first
      without its investigation FK, create `investigation` (which can now
      reference `alert`), then add the alert -> investigation FK via ALTER.
    * investigation_stage / tool_call are deferred to the orchestrator spec
      (0007); ingestion only creates the investigation row in `pending`.
    * Indexes mirror the ORM (index=True columns + __table_args__) so the
      migrated schema matches Base.metadata.create_all used in tests.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. alert — created first, WITHOUT the investigation FK (it doesn't exist yet).
    op.create_table(
        "alert",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'alertmanager'"),
        ),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("alert_name", sa.String(length=255), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'firing'"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "labels",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "annotations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("investigation_id", postgresql.UUID(as_uuid=True), nullable=True),
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
    op.create_index("ix_alert_tenant_id", "alert", ["tenant_id"])
    op.create_index("ix_alert_alert_name", "alert", ["alert_name"])
    op.create_index("ix_alert_investigation_id", "alert", ["investigation_id"])
    op.create_index(
        "ix_alert_tenant_fingerprint_received",
        "alert",
        ["tenant_id", "fingerprint", "received_at"],
    )

    # 2. investigation — can reference `alert` now.
    op.create_table(
        "investigation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "service_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "triggering_alert_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("alert.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("confidence", sa.String(length=16), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "budget_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "prompt_version",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'unknown'"),
        ),
        sa.Column(
            "code_version",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'unknown'"),
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
    )
    op.create_index("ix_investigation_tenant_id", "investigation", ["tenant_id"])
    op.create_index("ix_investigation_service_id", "investigation", ["service_id"])
    op.create_index("ix_investigation_fingerprint", "investigation", ["fingerprint"])
    op.create_index(
        "ix_investigation_tenant_status_started",
        "investigation",
        ["tenant_id", "status", "started_at"],
    )

    # 3. Close the circular FK: alert.investigation_id -> investigation.id.
    op.create_foreign_key(
        "fk_alert_investigation_id",
        "alert",
        "investigation",
        ["investigation_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_alert_investigation_id", "alert", type_="foreignkey")
    op.drop_index("ix_investigation_tenant_status_started", table_name="investigation")
    op.drop_index("ix_investigation_fingerprint", table_name="investigation")
    op.drop_index("ix_investigation_service_id", table_name="investigation")
    op.drop_index("ix_investigation_tenant_id", table_name="investigation")
    op.drop_table("investigation")
    op.drop_index("ix_alert_tenant_fingerprint_received", table_name="alert")
    op.drop_index("ix_alert_investigation_id", table_name="alert")
    op.drop_index("ix_alert_alert_name", table_name="alert")
    op.drop_index("ix_alert_tenant_id", table_name="alert")
    op.drop_table("alert")
