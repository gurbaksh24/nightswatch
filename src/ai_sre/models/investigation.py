"""Investigation + InvestigationStage + ToolCall.

A single investigation has many stages and many tool calls. Grouping these
models in one file because they form a tightly-coupled aggregate.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sre.db import Base
from ai_sre.models.base import IdMixin, TenantOwnedMixin, TimestampMixin


class Investigation(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "investigation"
    __table_args__ = (
        Index("ix_investigation_tenant_status_started", "tenant_id", "status", "started_at"),
    )

    service_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("service.id", ondelete="CASCADE"), nullable=False, index=True
    )
    triggering_alert_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("alert.id", ondelete="SET NULL"), nullable=True
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    confidence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    budget_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    code_version: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")


class InvestigationStage(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "investigation_stage"
    __table_args__ = (
        UniqueConstraint("investigation_id", "name", "attempt", name="uq_stage_per_attempt"),
    )

    investigation_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("investigation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class ToolCall(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "tool_call"
    __table_args__ = (
        Index("ix_tool_call_investigation_created", "investigation_id", "created_at"),
    )

    investigation_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("investigation.id", ondelete="CASCADE"), nullable=False
    )
    stage_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("investigation_stage.id", ondelete="CASCADE"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    input: Mapped[dict] = mapped_column(JSONB, nullable=False)
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
