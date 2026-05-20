"""Integration model. One row per connected external system."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ai_sre.db import Base
from ai_sre.models.base import IdMixin, TenantOwnedMixin, TimestampMixin


class Integration(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "integration"

    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    config_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    config_public: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    last_health_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    webhook_signing_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
