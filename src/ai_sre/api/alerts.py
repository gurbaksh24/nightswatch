"""Alert ingress webhook.

This endpoint:
    1. Verifies the HMAC signature against the tenant's webhook secret.
    2. Persists the raw payload.
    3. Hands off to `core.alert.service.AlertService.ingest` which dedupes
       and enqueues the investigation.
    4. Returns 202 fast (target p95 < 500 ms).

The endpoint is NOT bearer-auth — it uses a per-tenant signing secret.
See docs/05-api-spec.md §Webhook payload — signature scheme.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Header, Request, status

router = APIRouter()


@router.post(
    "/webhooks/alertmanager/{tenant_id}",
    status_code=status.HTTP_202_ACCEPTED,
)
async def alertmanager_webhook(
    tenant_id: UUID,
    request: Request,
    x_signature: str = Header(..., alias="X-AI-SRE-Signature"),
) -> dict:
    """Alertmanager v4+ webhook receiver. Tenant-scoped by URL + HMAC.

    The full body is read for signature verification before parsing.
    """
    # raw = await request.body()
    # TODO(spec-NNNN: alert-ingestion):
    #   1. Load tenant + signing secret
    #   2. verify_signature(secret, raw, x_signature)
    #   3. Parse AlertmanagerPayload (Pydantic)
    #   4. alert_service.ingest(tenant, payload, raw)
    #   5. Return {"accepted": N, "alert_ids": [...]}
    raise NotImplementedError
