"""FeedbackService.

Persists feedback events (from Slack buttons and direct API calls), serves
them to the dashboard, and exports them for eval/training.

Feedback `kind`: useful | not_useful | actual_cause | comment.

On `actual_cause` feedback, the linked investigation's "ground truth" is
recorded; this is what trains future quality metrics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from ai_sre.api.deps import TenantContext


class FeedbackService:
    async def record(
        self,
        tenant: TenantContext,
        *,
        investigation_id: UUID,
        kind: str,
        actor: str | None = None,
        body: str | None = None,
        metadata: dict | None = None,
    ) -> UUID:
        # TODO(spec-NNNN: feedback)
        raise NotImplementedError

    async def list_for_investigation(
        self, tenant: TenantContext, investigation_id: UUID
    ) -> list[object]:
        raise NotImplementedError
