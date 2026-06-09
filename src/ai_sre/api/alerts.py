"""Alert ingress webhook.

This endpoint:
    1. Reads the raw body and verifies the HMAC signature against the
       tenant's Prometheus webhook signing secret.
    2. Parses the Alertmanager v4 payload.
    3. Hands off to ``AlertService.ingest`` which persists, dedupes, and
       enqueues investigations.
    4. Returns 202 fast (target p95 < 500 ms).

It is NOT bearer-auth — it's scoped by the ``{tenant_id}`` in the URL plus
the per-tenant signing secret. See docs/05-api-spec.md "Webhook payload —
signature scheme".
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from ai_sre.api.deps import (
    get_alert_service,
    get_integration_service_for_tenant,
)
from ai_sre.core.alert.service import AlertService, AlertValidationError
from ai_sre.core.integration.service import IntegrationService
from ai_sre.exceptions import IntegrationCredentialDecryptionFailed
from ai_sre.schemas.alert import AlertmanagerPayload, AlertReceiveResponse
from ai_sre.utils.logging import get_logger
from ai_sre.utils.webhook_signature import verify_signature

logger = get_logger(__name__)

router = APIRouter()

_INVALID_SIGNATURE = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail={
        "code": "auth.invalid_signature",
        "message": "Webhook signature verification failed.",
    },
)


async def _resolve_webhook_secret(
    integration_service: IntegrationService,
) -> str | None:
    """Return the tenant's Prometheus webhook signing secret, or ``None``.

    ``None`` covers every "can't verify" case (no Prometheus integration, no
    secret set, decryption failure) so the caller maps them all to one 401
    without leaking which tenant/integration exists.
    """
    integrations = await integration_service.list()
    prometheus = next((i for i in integrations if i.kind == "prometheus"), None)
    if prometheus is None:
        return None
    try:
        return integration_service.get_webhook_secret(prometheus)
    except IntegrationCredentialDecryptionFailed:
        return None


@router.post(
    "/webhooks/alertmanager/{tenant_id}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AlertReceiveResponse,
    summary="Alertmanager v4+ webhook receiver (HMAC-signed, tenant-scoped by URL).",
)
async def alertmanager_webhook(
    tenant_id: UUID,
    request: Request,
    x_signature: str | None = Header(default=None, alias="X-AI-SRE-Signature"),
    integration_service: IntegrationService = Depends(
        get_integration_service_for_tenant
    ),
    alert_service: AlertService = Depends(get_alert_service),
) -> AlertReceiveResponse:
    """Receive, verify, parse, and ingest an Alertmanager webhook.

    Returns 202 with the persisted alert ids; 401 on signature failure;
    400 on a malformed payload.
    """
    raw = await request.body()

    secret = await _resolve_webhook_secret(integration_service)
    if secret is None or not verify_signature(secret, raw, x_signature):
        logger.warning("webhook.signature_invalid", tenant_id=str(tenant_id))
        raise _INVALID_SIGNATURE

    try:
        payload = AlertmanagerPayload.model_validate_json(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "validation.failed",
                "message": f"Invalid Alertmanager payload: {exc}",
            },
        ) from exc

    try:
        result = await alert_service.ingest(tenant_id, payload, raw)
    except AlertValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    return AlertReceiveResponse(
        accepted=len(result.alert_ids), alert_ids=result.alert_ids
    )
