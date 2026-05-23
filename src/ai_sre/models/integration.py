"""Integration model. One row per connected external system.

Table-level constraints (UNIQUE, CHECK) are declared here AND in the
Alembic migration. Keeping them in sync matters because the test conftest
uses ``Base.metadata.create_all`` and would otherwise build a table
without the production constraints — letting duplicate inserts succeed
silently in tests.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, LargeBinary, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ai_sre.db import Base
from ai_sre.models.base import IdMixin, TenantOwnedMixin, TimestampMixin


class Integration(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "integration"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "kind", "name", name="uq_integration_tenant_kind_name"
        ),
        CheckConstraint(
            "kind IN ('prometheus', 'slack')",
            name="ck_integration_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'healthy', 'unhealthy', 'disabled')",
            name="ck_integration_status",
        ),
    )

    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    config_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    config_public: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    last_health_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    webhook_signing_secret_encrypted: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
