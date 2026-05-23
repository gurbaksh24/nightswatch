"""Integration routes.

Implemented per :doc:`docs/05-api-spec.md` §Integrations:

    * ``POST   /v1/integrations``      — create (spec 0002)
    * ``GET    /v1/integrations``      — list (spec 0002)
    * ``GET    /v1/integrations/{id}`` — read (spec 0002)
    * ``DELETE /v1/integrations/{id}`` — delete (spec 0002)

Out-of-scope routes are kept as stubs; the spec that owns them fills them in:

    * ``POST /v1/integrations/{id}/health-check`` — spec 0003
    * Slack OAuth start / callback                 — spec 0010
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from ai_sre.api.deps import (
    TenantContext,
    current_tenant,
    get_integration_service,
)
from ai_sre.core.integration.service import IntegrationService
from ai_sre.exceptions import IntegrationAlreadyExists, IntegrationNotFound
from ai_sre.schemas.integration import (
    IntegrationCreateRequest,
    IntegrationResponse,
)

router = APIRouter()


@router.post(
    "/integrations",
    status_code=status.HTTP_201_CREATED,
    response_model=IntegrationResponse,
    summary="Create an integration (Prometheus, Slack, …). Health check follows asynchronously.",
)
async def create_integration(
    body: IntegrationCreateRequest,
    service: IntegrationService = Depends(get_integration_service),
) -> IntegrationResponse:
    """Persist a new integration with its config envelope-encrypted at rest.

    The response never includes the encrypted blob or any secret material —
    only the public view of the config (host, auth type).
    """
    try:
        row = await service.create(
            kind=body.kind,
            name=body.name,
            config=body.config.model_dump(),
        )
    except IntegrationAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc
    return IntegrationResponse.model_validate(row, from_attributes=True)


@router.get(
    "/integrations",
    response_model=list[IntegrationResponse],
    summary="List the calling tenant's integrations.",
)
async def list_integrations(
    service: IntegrationService = Depends(get_integration_service),
) -> list[IntegrationResponse]:
    """List all integrations for the calling tenant, newest first."""
    rows = await service.list()
    return [
        IntegrationResponse.model_validate(row, from_attributes=True) for row in rows
    ]


@router.get(
    "/integrations/{integration_id}",
    response_model=IntegrationResponse,
    summary="Read a single integration.",
)
async def get_integration(
    integration_id: UUID,
    service: IntegrationService = Depends(get_integration_service),
) -> IntegrationResponse:
    """Return the integration. 404 if it doesn't belong to this tenant."""
    row = await service.get(integration_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "integration.not_found",
                "message": "Integration not found.",
            },
        )
    return IntegrationResponse.model_validate(row, from_attributes=True)


@router.delete(
    "/integrations/{integration_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an integration.",
)
async def delete_integration(
    integration_id: UUID,
    service: IntegrationService = Depends(get_integration_service),
) -> None:
    """Hard-delete an integration. 404 if it doesn't belong to this tenant."""
    try:
        await service.delete(integration_id)
    except IntegrationNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return None


# ---- Out of scope for spec 0002 — kept as stubs ----


@router.post(
    "/integrations/{integration_id}/health-check",
    summary="Force a health-check (implemented in spec 0003).",
    include_in_schema=False,
)
async def force_health_check(
    integration_id: str,
    tenant: TenantContext = Depends(current_tenant),
) -> dict[str, Any]:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "code": "internal",
            "message": "Health-check ships in spec 0003 (Prometheus connector).",
        },
    )


@router.get(
    "/integrations/slack/oauth/start",
    summary="Start Slack OAuth (implemented in spec 0010).",
    include_in_schema=False,
)
async def slack_oauth_start(
    tenant: TenantContext = Depends(current_tenant),
) -> dict[str, Any]:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "code": "internal",
            "message": "Slack OAuth ships in spec 0010 (Slack delivery).",
        },
    )


@router.get(
    "/integrations/slack/oauth/callback",
    summary="Slack OAuth callback (implemented in spec 0010).",
    include_in_schema=False,
)
async def slack_oauth_callback() -> dict[str, Any]:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "code": "internal",
            "message": "Slack OAuth ships in spec 0010 (Slack delivery).",
        },
    )
