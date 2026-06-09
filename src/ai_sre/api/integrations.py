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
    get_job_queue,
)
from ai_sre.core.integration.service import IntegrationService
from ai_sre.exceptions import IntegrationAlreadyExists, IntegrationNotFound
from ai_sre.queue.base import JobQueue, JobQueueError
from ai_sre.schemas.integration import (
    IntegrationCreatedResponse,
    IntegrationCreateRequest,
    IntegrationResponse,
    WebhookSecretResponse,
)

router = APIRouter()


@router.post(
    "/integrations",
    status_code=status.HTTP_201_CREATED,
    response_model=IntegrationCreatedResponse,
    summary="Create an integration (Prometheus, Slack, …). Health check follows asynchronously.",
)
async def create_integration(
    body: IntegrationCreateRequest,
    service: IntegrationService = Depends(get_integration_service),
) -> IntegrationCreatedResponse:
    """Persist a new integration with its config envelope-encrypted at rest.

    The response never includes the encrypted config blob. For a Prometheus
    integration it does include a freshly generated webhook signing secret —
    returned **once** so the tenant can configure their Alertmanager (spec
    0006). Rotate it later via ``POST /v1/integrations/{id}/webhook-secret``.
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

    resp = IntegrationCreatedResponse.model_validate(row, from_attributes=True)
    if body.kind == "prometheus":
        resp.webhook_signing_secret = await service.generate_webhook_secret(row.id)
    return resp


@router.post(
    "/integrations/{integration_id}/webhook-secret",
    response_model=WebhookSecretResponse,
    summary="Rotate (or set) the webhook signing secret. Shown once.",
)
async def rotate_webhook_secret(
    integration_id: UUID,
    service: IntegrationService = Depends(get_integration_service),
) -> WebhookSecretResponse:
    """Generate a new webhook signing secret, replacing any existing one.

    The previous secret stops working immediately. 404 if the integration
    isn't owned by this tenant.
    """
    row = await service.get(integration_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "integration.not_found",
                "message": "Integration not found.",
            },
        )
    secret = await service.generate_webhook_secret(integration_id)
    return WebhookSecretResponse(
        integration_id=integration_id, webhook_signing_secret=secret
    )


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


# ---- Health check (spec 0003) ----


@router.post(
    "/integrations/{integration_id}/health-check",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue a health-check job for an integration.",
)
async def force_health_check(
    integration_id: UUID,
    tenant: TenantContext = Depends(current_tenant),
    service: IntegrationService = Depends(get_integration_service),
    job_queue: JobQueue = Depends(get_job_queue),
) -> dict[str, Any]:
    """Schedule an asynchronous health probe.

    The actual probe runs in the worker (``run_health_check`` task), which
    updates ``integration.status`` and ``last_health_check_at``. Poll
    ``GET /v1/integrations/{id}`` for the result.

    Returns 202 with the enqueued job id; 404 if the integration doesn't
    belong to this tenant.
    """
    row = await service.get(integration_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "integration.not_found",
                "message": "Integration not found.",
            },
        )
    try:
        job_id = await job_queue.enqueue(
            "health_check",
            {
                "tenant_id": str(tenant.tenant_id),
                "integration_id": str(integration_id),
            },
        )
    except JobQueueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "internal",
                "message": f"Could not enqueue health check: {exc}",
            },
        ) from exc
    return {
        "integration_id": str(integration_id),
        "job_id": job_id,
        "status": "queued",
    }


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
