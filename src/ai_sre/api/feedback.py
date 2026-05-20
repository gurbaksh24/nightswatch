"""Feedback routes.

See docs/05-api-spec.md §Feedback.

The Slack callback endpoint (which produces most feedback events in practice)
lives in delivery/slack handling, not here. This router serves dashboard /
programmatic feedback.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ai_sre.api.deps import TenantContext, current_tenant

router = APIRouter()


@router.post("/investigations/{investigation_id}/feedback", status_code=201)
async def submit_feedback(
    investigation_id: str, tenant: TenantContext = Depends(current_tenant)
) -> dict:
    raise NotImplementedError


@router.get("/investigations/{investigation_id}/feedback")
async def list_feedback(
    investigation_id: str, tenant: TenantContext = Depends(current_tenant)
) -> dict:
    raise NotImplementedError
