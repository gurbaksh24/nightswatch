"""FeedbackService.

Persists feedback events (from Slack buttons and direct API calls) and serves
them to the dashboard. Feedback ``kind``: useful | not_useful | actual_cause |
comment.

Tenant-scoped via the repository (no tenant argument is threaded through).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from ai_sre.core.feedback.repository import FeedbackRepository
from ai_sre.models.feedback import Feedback
from ai_sre.utils.logging import get_logger

logger = get_logger(__name__)


class FeedbackService:
    """Record and read feedback for the tenant's investigations."""

    def __init__(self, repo: FeedbackRepository) -> None:
        self.repo = repo

    async def record(
        self,
        *,
        investigation_id: UUID,
        kind: str,
        actor: str | None = None,
        body: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Feedback:
        """Persist one feedback event and return the row."""
        row = await self.repo.create(
            investigation_id=investigation_id,
            kind=kind,
            actor=actor,
            body=body,
            metadata=metadata,
        )
        logger.info(
            "feedback.recorded",
            tenant_id=str(self.repo.tenant_id),
            investigation_id=str(investigation_id),
            kind=kind,
        )
        return row

    async def list_for_investigation(
        self, investigation_id: UUID
    ) -> Sequence[Feedback]:
        return await self.repo.list_for_investigation(investigation_id)
