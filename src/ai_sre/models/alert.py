"""Alert: raw + normalised payload received from a webhook."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sre.db import Base
from ai_sre.models.base import IdMixin, TenantOwnedMixin, TimestampMixin


class Alert(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "alert"
    __table_args__ = (
        Index("ix_alert_tenant_fingerprint_received", "tenant_id", "fingerprint", "received_at"),
    )

    source: Mapped[str] = mapped_column(String(32), nullable=False, default="alertmanager")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    alert_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    severity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="firing")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    labels: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    annotations: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    investigation_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("investigation.id", ondelete="SET NULL"), nullable=True, index=True
    )
