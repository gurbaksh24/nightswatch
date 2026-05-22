"""Tenant + API-key routes.

Per docs/05-api-spec.md. Spec 0001 implements the full set:
    * POST /v1/tenant            (admin only — creates a tenant)
    * GET  /v1/tenant            (authenticated)
    * PATCH /v1/tenant           (authenticated)
    * POST   /v1/auth/api-keys
    * GET    /v1/auth/api-keys
    * DELETE /v1/auth/api-keys/{id}
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ai_sre.api.deps import (
    TenantContext,
    admin_only,
    current_tenant,
    get_api_key_service,
    get_tenant_service,
)
from ai_sre.core.tenant.api_key_service import ApiKeyService
from ai_sre.core.tenant.service import TenantService
from ai_sre.exceptions import TenantNotFound, TenantSlugTaken
from ai_sre.schemas.api_key import (
    ApiKeyCreateRequest,
    ApiKeyIssuedResponse,
    ApiKeyResponse,
)
from ai_sre.schemas.tenant import (
    TenantCreateRequest,
    TenantResponse,
    TenantUpdateRequest,
)

router = APIRouter()


# ---- Tenants ----


@router.post(
    "/tenant",
    dependencies=[Depends(admin_only)],
    status_code=status.HTTP_201_CREATED,
    response_model=TenantResponse,
    summary="Create a new tenant (admin only).",
)
async def create_tenant(
    body: TenantCreateRequest,
    service: TenantService = Depends(get_tenant_service),
) -> TenantResponse:
    """Admin-only endpoint: create a new tenant. Returns the persisted row."""
    try:
        tenant = await service.create(name=body.name, slug=body.slug)
    except TenantSlugTaken as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc
    return TenantResponse.model_validate(tenant, from_attributes=True)


@router.get(
    "/tenant",
    response_model=TenantResponse,
    summary="Get the calling tenant's profile.",
)
async def get_tenant(
    tenant: TenantContext = Depends(current_tenant),
    service: TenantService = Depends(get_tenant_service),
) -> TenantResponse:
    """Return the profile of the authenticated tenant."""
    row = await service.get(tenant.tenant_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "tenant.not_found", "message": "Tenant not found."},
        )
    return TenantResponse.model_validate(row, from_attributes=True)


@router.patch(
    "/tenant",
    response_model=TenantResponse,
    summary="Update the calling tenant's profile.",
)
async def update_tenant(
    body: TenantUpdateRequest,
    tenant: TenantContext = Depends(current_tenant),
    service: TenantService = Depends(get_tenant_service),
) -> TenantResponse:
    """Update mutable fields on the calling tenant."""
    try:
        row = await service.update(tenant.tenant_id, name=body.name)
    except TenantNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return TenantResponse.model_validate(row, from_attributes=True)


# ---- API keys ----


@router.post(
    "/auth/api-keys",
    status_code=status.HTTP_201_CREATED,
    response_model=ApiKeyIssuedResponse,
    summary="Issue a new API key for the calling tenant.",
)
async def create_api_key(
    body: ApiKeyCreateRequest,
    tenant: TenantContext = Depends(current_tenant),
    service: ApiKeyService = Depends(get_api_key_service),
) -> ApiKeyIssuedResponse:
    """Issue a new API key. The plaintext key is returned only in this response."""
    issued = await service.issue(tenant.tenant_id, name=body.name)
    return ApiKeyIssuedResponse(
        id=issued.id,
        key=issued.key,
        prefix=issued.prefix,
        created_at=issued.created_at,
    )


@router.get(
    "/auth/api-keys",
    response_model=list[ApiKeyResponse],
    summary="List the calling tenant's API keys (without secrets).",
)
async def list_api_keys(
    tenant: TenantContext = Depends(current_tenant),
    service: ApiKeyService = Depends(get_api_key_service),
) -> list[ApiKeyResponse]:
    """Return all API keys for the calling tenant, newest first."""
    rows = await service.list(tenant.tenant_id)
    return [ApiKeyResponse.model_validate(row, from_attributes=True) for row in rows]


@router.delete(
    "/auth/api-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an API key.",
)
async def revoke_api_key(
    key_id: UUID,
    tenant: TenantContext = Depends(current_tenant),
    service: ApiKeyService = Depends(get_api_key_service),
) -> None:
    """Revoke a key owned by the calling tenant. Idempotent."""
    await service.revoke(tenant.tenant_id, key_id)
    return None
