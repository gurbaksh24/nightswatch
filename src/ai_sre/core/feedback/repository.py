"""FeedbackRepository — tenant-scoped CRUD for feedback events."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sre.core._base.repository import TenantScopedRepository
from ai_sre.models.feedback import Feedback


class FeedbackRepository(TenantScopedRepository[Feedback]):
    """CRUD for :class:`Feedback`, scoped to a single tenant."""

    model = Feedback

    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        super().__init__(session, tenant_id)

    async def create(
        self,
        *,
        investigation_id: UUID,
        kind: str,
        actor: str | None = None,
        body: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Feedback:
        row = Feedback(
            tenant_id=self.tenant_id,
            investigation_id=investigation_id,
            kind=kind,
            actor=actor,
            body=body,
            extra_metadata=metadata or {},
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def list_for_investigation(
        self, investigation_id: UUID
    ) -> Sequence[Feedback]:
        """All feedback for an investigation, oldest first."""
        stmt = (
            self._scoped(select(Feedback))
            .where(Feedback.investigation_id == investigation_id)
            .order_by(Feedback.created_at)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()
