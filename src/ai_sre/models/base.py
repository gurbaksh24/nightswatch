"""Shared mixins and column helpers for ORM models.

Conventions (per data-model doc):
    * PK: UUID via `id`.
    * Timestamps: `created_at`, `updated_at` always present.
    * Soft delete: `deleted_at` on entities that support it.

Models live one-per-file under `models/`. See `docs/04-data-model.md` for the
schema.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sre.utils.ids import new_id


class IdMixin:
    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=new_id
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TenantOwnedMixin:
    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
