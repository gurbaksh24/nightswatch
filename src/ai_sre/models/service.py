"""Subject service + topology + metric catalog."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sre.db import Base
from ai_sre.models.base import IdMixin, TenantOwnedMixin, TimestampMixin


class Service(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "service"
    # MVP: one subject service per tenant. Enforced both here (for
    # Base.metadata.create_all in tests) and in migration 0004.
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_service_tenant_id"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    label_selector: Mapped[dict] = mapped_column(JSONB, nullable=False)
    ownership: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    slo_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class ServiceDependency(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "service_dependency"

    service_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("service.id", ondelete="CASCADE"), nullable=False, index=True
    )
    direction: Mapped[str] = mapped_column(String(16), nullable=False)   # upstream / downstream
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    extra_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    confirmed_by_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class MetricCatalogEntry(IdMixin, TenantOwnedMixin, TimestampMixin, Base):
    __tablename__ = "metric_catalog_entry"

    service_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("service.id", ondelete="CASCADE"), nullable=False, index=True
    )
    metric_name: Mapped[str] = mapped_column(String(255), nullable=False)
    metric_type: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    labels: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    help_text: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
