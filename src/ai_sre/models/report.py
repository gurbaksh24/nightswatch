"""Report: final structured RCA output of an investigation."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sre.db import Base
from ai_sre.models.base import IdMixin, TenantOwnedMixin, TimestampMixin


class Report(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "report"
    __table_args__ = (
        UniqueConstraint("investigation_id", name="uq_report_per_investigation"),
    )

    investigation_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("investigation.id", ondelete="CASCADE"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="v1")
    headline: Mapped[str] = mapped_column(String(2048), nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    hypotheses: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    next_actions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    related_incidents: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_receipts: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
