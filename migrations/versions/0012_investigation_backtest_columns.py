"""investigation backtest/replay columns

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-11

Spec: specs/0016-backtest-and-replay.md

Adds is_backtest, dry_run, and replayed_from to ``investigation`` so a past
alert can be re-run (optionally without delivery) and an existing
investigation can be replayed as a new one.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "investigation",
        sa.Column(
            "is_backtest",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "investigation",
        sa.Column(
            "dry_run",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "investigation",
        sa.Column(
            "replayed_from",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("investigation.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("investigation", "replayed_from")
    op.drop_column("investigation", "dry_run")
    op.drop_column("investigation", "is_backtest")
