"""Investigation routes.

See docs/05-api-spec.md §Investigations.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ai_sre.api.deps import TenantContext, current_tenant

router = APIRouter()


@router.get("/investigations")
async def list_investigations(
    tenant: TenantContext = Depends(current_tenant),
) -> dict:
    """List investigations with optional filters: status, service_id, from, to, confidence."""
    raise NotImplementedError


@router.get("/investigations/{investigation_id}")
async def get_investigation(
    investigation_id: str, tenant: TenantContext = Depends(current_tenant)
) -> dict:
    raise NotImplementedError


@router.get("/investigations/{investigation_id}/trace")
async def get_investigation_trace(
    investigation_id: str, tenant: TenantContext = Depends(current_tenant)
) -> dict:
    """Full audit trail: stages, prompts, tool calls, LLM responses, errors."""
    raise NotImplementedError


@router.get("/investigations/{investigation_id}/report")
async def get_report(
    investigation_id: str, tenant: TenantContext = Depends(current_tenant)
) -> dict:
    raise NotImplementedError


@router.post("/investigations/{investigation_id}/replay", status_code=202)
async def replay_investigation(
    investigation_id: str, tenant: TenantContext = Depends(current_tenant)
) -> dict:
    """Re-run the investigation with current code & prompts."""
    raise NotImplementedError


@router.post("/investigations/backtest", status_code=202)
async def backtest(tenant: TenantContext = Depends(current_tenant)) -> dict:
    """Submit a raw Alertmanager payload (or reference a stored alert) for diagnosis."""
    raise NotImplementedError
