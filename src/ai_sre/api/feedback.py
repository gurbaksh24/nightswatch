"""Feedback routes (spec 0013).

    * POST /v1/investigations/{id}/feedback  — record feedback.
    * GET  /v1/investigations/{id}/feedback  — list feedback.

Most feedback in practice arrives via the Slack callback (api/delivery.py);
these serve the dashboard / programmatic clients.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ai_sre.api.deps import (
    get_feedback_service,
    get_investigation_repo,
)
from ai_sre.core.feedback.service import FeedbackService
from ai_sre.core.investigation.repository import InvestigationRepository
from ai_sre.schemas.feedback import FeedbackCreateRequest, FeedbackResponse

router = APIRouter()

_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail={"code": "investigation.not_found", "message": "Investigation not found."},
)


@router.post(
    "/investigations/{investigation_id}/feedback",
    status_code=status.HTTP_201_CREATED,
    response_model=FeedbackResponse,
    summary="Record feedback on an investigation.",
)
async def submit_feedback(
    investigation_id: UUID,
    body: FeedbackCreateRequest,
    repo: InvestigationRepository = Depends(get_investigation_repo),
    feedback: FeedbackService = Depends(get_feedback_service),
) -> FeedbackResponse:
    """Persist a feedback event. 404 if the investigation isn't owned here."""
    if await repo.get(investigation_id) is None:
        raise _NOT_FOUND
    row = await feedback.record(
        investigation_id=investigation_id,
        kind=body.kind,
        actor=body.actor,
        body=body.body,
    )
    return FeedbackResponse.model_validate(row, from_attributes=True)


@router.get(
    "/investigations/{investigation_id}/feedback",
    response_model=list[FeedbackResponse],
    summary="List feedback for an investigation.",
)
async def list_feedback(
    investigation_id: UUID,
    repo: InvestigationRepository = Depends(get_investigation_repo),
    feedback: FeedbackService = Depends(get_feedback_service),
) -> list[FeedbackResponse]:
    if await repo.get(investigation_id) is None:
        raise _NOT_FOUND
    rows = await feedback.list_for_investigation(investigation_id)
    return [FeedbackResponse.model_validate(r, from_attributes=True) for r in rows]
