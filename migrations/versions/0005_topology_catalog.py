"""service_dependency + metric_catalog_entry tables

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-08

Spec: specs/0005-topology-and-catalog.md

Notes:
    * Both tables are modelled in ORM since spec 0004 but were never
      migrated — this migration creates them. (The spec's "migration
      probably none" note was wrong; only the ``service`` table existed.)
    * ``service_dependency`` carries ``last_seen_at`` so a refresh can mark
      edges stale (kept, not deleted) when they stop appearing — important
      for user-confirmed edges.
    * Unique keys mirror the ORM ``__table_args__`` so ``create_all`` (tests)
      and the migration (deployments) stay in sync.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_dependency",
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
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "extra_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "confirmed_by_user",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
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
            "service_id", "direction", "name", name="uq_service_dependency_edge"
        ),
        sa.CheckConstraint(
            "direction IN ('upstream', 'downstream')",
            name="ck_service_dependency_direction",
        ),
    )
    op.create_index(
        "ix_service_dependency_tenant_id", "service_dependency", ["tenant_id"]
    )
    op.create_index(
        "ix_service_dependency_service_id", "service_dependency", ["service_id"]
    )

    op.create_table(
        "metric_catalog_entry",
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
        sa.Column("metric_name", sa.String(length=255), nullable=False),
        sa.Column(
            "metric_type",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'unknown'"),
        ),
        sa.Column(
            "labels",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("help_text", sa.String(length=1024), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
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
            "service_id", "metric_name", name="uq_metric_catalog_service_metric"
        ),
    )
    op.create_index(
        "ix_metric_catalog_entry_tenant_id", "metric_catalog_entry", ["tenant_id"]
    )
    op.create_index(
        "ix_metric_catalog_entry_lookup",
        "metric_catalog_entry",
        ["tenant_id", "service_id", "metric_name"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_metric_catalog_entry_lookup", table_name="metric_catalog_entry"
    )
    op.drop_index(
        "ix_metric_catalog_entry_tenant_id", table_name="metric_catalog_entry"
    )
    op.drop_table("metric_catalog_entry")
    op.drop_index(
        "ix_service_dependency_service_id", table_name="service_dependency"
    )
    op.drop_index(
        "ix_service_dependency_tenant_id", table_name="service_dependency"
    )
    op.drop_table("service_dependency")
