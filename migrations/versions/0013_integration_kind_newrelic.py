"""Widen ck_integration_kind to allow 'newrelic' (spec 0019).

Constraint-only change: drop + recreate the CHECK with the new member.
Instant on any table size (validation scans existing rows, all of which
already satisfy the wider constraint).

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | None = None
depends_on: str | None = None

_KINDS_NEW = "kind IN ('prometheus', 'slack', 'newrelic')"
_KINDS_OLD = "kind IN ('prometheus', 'slack')"


def upgrade() -> None:
    op.drop_constraint("ck_integration_kind", "integration", type_="check")
    op.create_check_constraint("ck_integration_kind", "integration", _KINDS_NEW)


def downgrade() -> None:
    # Fails (correctly) if newrelic rows exist — delete them first.
    op.drop_constraint("ck_integration_kind", "integration", type_="check")
    op.create_check_constraint("ck_integration_kind", "integration", _KINDS_OLD)
