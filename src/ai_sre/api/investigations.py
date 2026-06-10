"""Investigation read routes (spec 0012).

    * GET /v1/investigations              — list (filters + paging)
    * GET /v1/investigations/{id}         — detail (+ report if present)
    * GET /v1/investigations/{id}/report  — the structured RCA
    * GET /v1/investigations/{id}/trace   — stages + tool calls

Replay + backtest are stubs until spec 0016.

See docs/05-api-spec.md §Investigations.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ai_sre.api.deps import (
    TenantContext,
    current_tenant,
    get_investigation_repo,
    get_report_repo,
)
from ai_sre.core.investigation.repository import (
    InvestigationRepository,
    ReportRepository,
)
from ai_sre.schemas.investigation import (
    InvestigationDetail,
    InvestigationSummary,
    ReportResponse,
    StageTrace,
    ToolCallTrace,
    TraceResponse,
)

router = APIRouter()

_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail={"code": "investigation.not_found", "message": "Investigation not found."},
)


@router.get(
    "/investigations",
    response_model=list[InvestigationSummary],
    summary="List investigations (filters: status, service_id, from, to, confidence).",
)
async def list_investigations(
    repo: InvestigationRepository = Depends(get_investigation_repo),
    status_filter: str | None = Query(default=None, alias="status"),
    service_id: UUID | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    confidence: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[InvestigationSummary]:
    """List the calling tenant's investigations, newest first."""
    rows = await repo.list_filtered(
        status=status_filter,
        service_id=service_id,
        since=from_,
        until=to,
        confidence=confidence,
        limit=limit,
        offset=offset,
    )
    return [InvestigationSummary.model_validate(r, from_attributes=True) for r in rows]


@router.get(
    "/investigations/{investigation_id}",
    response_model=InvestigationDetail,
    summary="Read an investigation, including its report if available.",
)
async def get_investigation(
    investigation_id: UUID,
    repo: InvestigationRepository = Depends(get_investigation_repo),
    report_repo: ReportRepository = Depends(get_report_repo),
) -> InvestigationDetail:
    inv = await repo.get(investigation_id)
    if inv is None:
        raise _NOT_FOUND
    detail = InvestigationDetail.model_validate(inv, from_attributes=True)
    report = await report_repo.get_for_investigation(investigation_id)
    if report is not None:
        detail.report = ReportResponse.model_validate(report, from_attributes=True)
    return detail


@router.get(
    "/investigations/{investigation_id}/report",
    response_model=ReportResponse,
    summary="The structured RCA report for an investigation.",
)
async def get_report(
    investigation_id: UUID,
    repo: InvestigationRepository = Depends(get_investigation_repo),
    report_repo: ReportRepository = Depends(get_report_repo),
) -> ReportResponse:
    if await repo.get(investigation_id) is None:
        raise _NOT_FOUND
    report = await report_repo.get_for_investigation(investigation_id)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "report.not_found", "message": "No report yet for this investigation."},
        )
    return ReportResponse.model_validate(report, from_attributes=True)


@router.get(
    "/investigations/{investigation_id}/trace",
    response_model=TraceResponse,
    summary="Full audit trail: every stage and tool call.",
)
async def get_investigation_trace(
    investigation_id: UUID,
    repo: InvestigationRepository = Depends(get_investigation_repo),
) -> TraceResponse:
    if await repo.get(investigation_id) is None:
        raise _NOT_FOUND
    stages = await repo.list_stages(investigation_id)
    tool_calls = await repo.list_tool_calls(investigation_id)
    return TraceResponse(
        investigation_id=investigation_id,
        stages=[StageTrace.model_validate(s, from_attributes=True) for s in stages],
        tool_calls=[
            ToolCallTrace.model_validate(t, from_attributes=True) for t in tool_calls
        ],
    )


# ---- Replay / backtest (spec 0016) ----


@router.post(
    "/investigations/{investigation_id}/replay",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    include_in_schema=False,
)
async def replay_investigation(
    investigation_id: str, tenant: TenantContext = Depends(current_tenant)
) -> dict[str, Any]:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={"code": "internal", "message": "Replay ships in spec 0016."},
    )


@router.post(
    "/investigations/backtest",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    include_in_schema=False,
)
async def backtest(tenant: TenantContext = Depends(current_tenant)) -> dict[str, Any]:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={"code": "internal", "message": "Backtest ships in spec 0016."},
    )
