"""InvestigationRepository — tenant-scoped access to the investigation table.

Spec 0006 needs two operations: find an active investigation for dedupe, and
create a new ``pending`` one. The orchestrator (spec 0007) will grow this with
status transitions and stage persistence; the contract is kept minimal here.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sre.core._base.repository import TenantScopedRepository
from ai_sre.models.investigation import Investigation

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
