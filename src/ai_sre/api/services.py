"""Subject service routes (topology and metric catalog).

See docs/05-api-spec.md §Services.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ai_sre.api.deps import TenantContext, current_tenant

router = APIRouter()


@router.post("/services", status_code=201)
async def register_service(tenant: TenantContext = Depends(current_tenant)) -> dict:
    """Register the subject service (one per tenant in MVP)."""
    raise NotImplementedError


@router.get("/services/{service_id}")
async def get_service(
    service_id: str, tenant: TenantContext = Depends(current_tenant)
) -> dict:
    raise NotImplementedError


@router.get("/services/{service_id}/topology")
async def get_topology(
    service_id: str, tenant: TenantContext = Depends(current_tenant)
) -> dict:
    raise NotImplementedError


@router.patch("/services/{service_id}/topology")
async def update_topology(
    service_id: str, tenant: TenantContext = Depends(current_tenant)
) -> dict:
    raise NotImplementedError


@router.post("/services/{service_id}/topology/refresh", status_code=202)
async def refresh_topology(
    service_id: str, tenant: TenantContext = Depends(current_tenant)
) -> dict:
    raise NotImplementedError


@router.get("/services/{service_id}/metrics")
async def list_metrics(
    service_id: str, tenant: TenantContext = Depends(current_tenant)
) -> dict:
    raise NotImplementedError


@router.post("/services/{service_id}/metrics/refresh", status_code=202)
async def refresh_metrics(
    service_id: str, tenant: TenantContext = Depends(current_tenant)
) -> dict:
    raise NotImplementedError
