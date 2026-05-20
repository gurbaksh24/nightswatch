"""Tenant routes.

See docs/05-api-spec.md for the full contract. Spec 0001 implements creation
and read; further routes (update, delete) land in later specs.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ai_sre.api.deps import TenantContext, admin_only, current_tenant

router = APIRouter()


@router.post(
    "/tenant",
    dependencies=[Depends(admin_only)],
    status_code=201,
    summary="Create a new tenant (admin only).",
)
async def create_tenant() -> dict:
    # TODO(spec-0001)
    raise NotImplementedError


@router.get("/tenant", summary="Get the calling tenant's profile.")
async def get_tenant(tenant: TenantContext = Depends(current_tenant)) -> dict:
    # TODO(spec-0001)
    raise NotImplementedError


@router.patch("/tenant", summary="Update the calling tenant's profile.")
async def update_tenant(tenant: TenantContext = Depends(current_tenant)) -> dict:
    # TODO(spec-0001)
    raise NotImplementedError


# ---- API keys ----


@router.post("/auth/api-keys", status_code=201, summary="Issue a new API key.")
async def create_api_key(tenant: TenantContext = Depends(current_tenant)) -> dict:
    # TODO(spec-0001)
    raise NotImplementedError


@router.get("/auth/api-keys", summary="List API keys (without secret).")
async def list_api_keys(tenant: TenantContext = Depends(current_tenant)) -> dict:
    # TODO(spec-0001)
    raise NotImplementedError


@router.delete("/auth/api-keys/{key_id}", status_code=204, summary="Revoke an API key.")
async def revoke_api_key(
    key_id: str, tenant: TenantContext = Depends(current_tenant)
) -> None:
    # TODO(spec-0001)
    raise NotImplementedError
