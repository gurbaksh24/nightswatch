"""integration table

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-23

Spec: specs/0002-integrations.md
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "integration",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("config_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column(
            "config_public",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("last_health_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "webhook_signing_secret_encrypted", sa.LargeBinary(), nullable=True
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
        sa.UniqueConstraint(
            "tenant_id", "kind", "name", name="uq_integration_tenant_kind_name"
        ),
        sa.CheckConstraint(
            "kind IN ('prometheus', 'slack')",
            name="ck_integration_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'healthy', 'unhealthy', 'disabled')",
            name="ck_integration_status",
        ),
    )
    op.create_index("ix_integration_tenant_id", "integration", ["tenant_id"])
    op.create_index("ix_integration_kind", "integration", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_integration_kind", table_name="integration")
    op.drop_index("ix_integration_tenant_id", table_name="integration")
    op.drop_table("integration")
