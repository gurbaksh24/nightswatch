"""AlertRepository — tenant-scoped persistence for received alerts."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sre.core._base.repository import TenantScopedRepository
from ai_sre.models.alert import Alert


class AlertRepository(TenantScopedRepository[Alert]):
    """CRUD for :class:`Alert`, scoped to a single tenant."""

    model = Alert

    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        super().__init__(session, tenant_id)

    async def create(
        self,
        *,
        source: str,
        received_at: datetime,
        raw_payload: dict[str, Any],
        alert_name: str,
        severity: str | None,
        status: str,
        started_at: datetime,
        ended_at: datetime | None,
        labels: dict[str, str],
        annotations: dict[str, str],
        fingerprint: str,
    ) -> Alert:
        """Insert one alert row (initially unlinked to an investigation)."""
        row = Alert(
            tenant_id=self.tenant_id,
            source=source,
            received_at=received_at,
            raw_payload=raw_payload,
            alert_name=alert_name,
            severity=severity,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            labels=labels,
            annotations=annotations,
            fingerprint=fingerprint,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def set_investigation(self, alert: Alert, investigation_id: UUID) -> None:
        """Link an already-persisted alert to an investigation."""
        alert.investigation_id = investigation_id
        await self.session.flush()

    async def count_by_fingerprint(self, fingerprint: str, *, since: datetime) -> int:
        """Count alert rows for a fingerprint received at/after ``since``.

        Used by Triage's flap/noise heuristic — flapping shows up as many
        alert rows even when dedup collapses them onto one investigation.
        """
        stmt = (
            select(func.count())
            .select_from(Alert)
            .where(
                Alert.tenant_id == self.tenant_id,
                Alert.fingerprint == fingerprint,
                Alert.received_at >= since,
            )
        )
        return int((await self.session.execute(stmt)).scalar_one())
