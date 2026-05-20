"""Integration routes.

See docs/05-api-spec.md §Integrations.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ai_sre.api.deps import TenantContext, current_tenant

router = APIRouter()


@router.post("/integrations", status_code=201)
async def create_integration(tenant: TenantContext = Depends(current_tenant)) -> dict:
    """Create an integration (Prometheus, Slack, …). Async health check follows."""
    # TODO(spec-NNNN: integrations)
    raise NotImplementedError


@router.get("/integrations")
async def list_integrations(tenant: TenantContext = Depends(current_tenant)) -> dict:
    raise NotImplementedError


@router.get("/integrations/{integration_id}")
async def get_integration(
    integration_id: str, tenant: TenantContext = Depends(current_tenant)
) -> dict:
    raise NotImplementedError


@router.post("/integrations/{integration_id}/health-check")
async def force_health_check(
    integration_id: str, tenant: TenantContext = Depends(current_tenant)
) -> dict:
    raise NotImplementedError


@router.delete("/integrations/{integration_id}", status_code=204)
async def delete_integration(
    integration_id: str, tenant: TenantContext = Depends(current_tenant)
) -> None:
    raise NotImplementedError


# ---- Slack OAuth ----


@router.get("/integrations/slack/oauth/start")
async def slack_oauth_start(tenant: TenantContext = Depends(current_tenant)) -> dict:
    raise NotImplementedError


@router.get("/integrations/slack/oauth/callback")
async def slack_oauth_callback() -> dict:
    """Public Slack OAuth callback. State token carries tenant context."""
    raise NotImplementedError
