"""InvestigationRepository — tenant-scoped access to the investigation table.

Spec 0006 needs two operations: find an active investigation for dedupe, and
create a new ``pending`` one. The orchestrator (spec 0007) will grow this with
status transitions and stage persistence; the contract is kept minimal here.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sre.core._base.repository import TenantScopedRepository
from ai_sre.models.investigation import Investigation, InvestigationStage

# An alert dedupes onto an investigation in any of these states. A `failed`
# investigation is intentionally excluded — a repeat alert should start a
# fresh attempt rather than attach to a dead run (spec 0006 edge case).
ACTIVE_DEDUPE_STATUSES: tuple[str, ...] = (
    "pending",
    "running",
    "succeeded",
    "partial",
)


class InvestigationRepository(TenantScopedRepository[Investigation]):
    """CRUD for :class:`Investigation`, scoped to a single tenant."""

    model = Investigation

    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        super().__init__(session, tenant_id)

    async def find_active_by_fingerprint(
        self, fingerprint: str, *, since: datetime
    ) -> Investigation | None:
        """Most recent non-failed investigation for this fingerprint created
        at/after ``since`` (the dedupe-window start), or ``None``.

        ``created_at`` is used as the window anchor — a ``pending``
        investigation has no ``started_at`` yet, and creation time is the
        moment dedup should reckon from.
        """
        stmt = (
            self._scoped(select(Investigation))
            .where(
                Investigation.fingerprint == fingerprint,
                Investigation.status.in_(ACTIVE_DEDUPE_STATUSES),
                Investigation.created_at >= since,
            )
            .order_by(Investigation.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_pending(
        self,
        *,
        service_id: UUID,
        triggering_alert_id: UUID,
        fingerprint: str,
    ) -> Investigation:
        """Create a new investigation in ``pending`` state."""
        row = Investigation(
            tenant_id=self.tenant_id,
            service_id=service_id,
            triggering_alert_id=triggering_alert_id,
            fingerprint=fingerprint,
            status="pending",
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    # ---- orchestrator support (spec 0007) ----

    async def set_status(
        self,
        investigation: Investigation,
        *,
        status: str,
        confidence: str | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        budget_snapshot: dict[str, Any] | None = None,
    ) -> Investigation:
        """Update mutable investigation fields and flush."""
        investigation.status = status
        if confidence is not None:
            investigation.confidence = confidence
        if started_at is not None:
            investigation.started_at = started_at
        if completed_at is not None:
            investigation.completed_at = completed_at
        if budget_snapshot is not None:
            investigation.budget_snapshot = budget_snapshot
        await self.session.flush()
        return investigation

    async def get_completed_stage_names(self, investigation_id: UUID) -> set[str]:
        """Names of stages already recorded as ``succeeded`` — the basis for
        idempotent resume after a worker restart."""
        stmt = select(InvestigationStage.name).where(
            InvestigationStage.tenant_id == self.tenant_id,
            InvestigationStage.investigation_id == investigation_id,
            InvestigationStage.status == "succeeded",
        )
        result = await self.session.execute(stmt)
        return set(result.scalars().all())

    async def upsert_stage(
        self,
        investigation_id: UUID,
        *,
        name: str,
        status: str,
        output: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        attempt: int = 1,
    ) -> InvestigationStage:
        """Insert or update the ``(investigation, name, attempt)`` stage row.

        Idempotent: re-running a stage (e.g. after a crash) updates the
        existing row rather than violating the unique constraint.
        """
        stmt = select(InvestigationStage).where(
            InvestigationStage.tenant_id == self.tenant_id,
            InvestigationStage.investigation_id == investigation_id,
            InvestigationStage.name == name,
            InvestigationStage.attempt == attempt,
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = InvestigationStage(
                tenant_id=self.tenant_id,
                investigation_id=investigation_id,
                name=name,
                attempt=attempt,
                status=status,
                output=output,
                error=error,
                started_at=started_at,
                completed_at=completed_at,
            )
            self.session.add(row)
        else:
            row.status = status
            row.output = output
            row.error = error
            if started_at is not None:
                row.started_at = started_at
            if completed_at is not None:
                row.completed_at = completed_at
        await self.session.flush()
        return row

    async def find_by_fingerprint(
        self,
        fingerprint: str,
        *,
        since: datetime,
        exclude_id: UUID | None = None,
        statuses: Iterable[str] | None = None,
    ) -> Sequence[Investigation]:
        """Investigations for this fingerprint created at/after ``since``,
        newest first. Used by Triage for known-issue + noise detection."""
        stmt = self._scoped(select(Investigation)).where(
            Investigation.fingerprint == fingerprint,
            Investigation.created_at >= since,
        )
        if exclude_id is not None:
            stmt = stmt.where(Investigation.id != exclude_id)
        if statuses is not None:
            stmt = stmt.where(Investigation.status.in_(tuple(statuses)))
        stmt = stmt.order_by(Investigation.created_at.desc())
        result = await self.session.execute(stmt)
        return result.scalars().all()
