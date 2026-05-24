"""subject service table

Revision ID: 0004
Revises: 0002
Create Date: 2026-05-25

Spec: specs/0004-service-registration.md

Notes:
    * One subject service per tenant in MVP — enforced by a UNIQUE constraint
      on ``tenant_id``. If we ever support multiple services per tenant we'll
      drop this in a follow-up migration.
    * ``slo_config`` carries the ``validation_pending`` flag (a single bit
      doesn't justify its own column).
    * The ``service_dependency`` and ``metric_catalog_entry`` tables ship
      with spec 0005; they're modelled in ORM but not migrated yet.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "label_selector",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "ownership",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "slo_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
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
        sa.UniqueConstraint("tenant_id", name="uq_service_tenant_id"),
    )
    op.create_index("ix_service_tenant_id", "service", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_service_tenant_id", table_name="service")
    op.drop_table("service")
