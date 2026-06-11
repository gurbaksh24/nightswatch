"""Slack interactive-callback receiver (spec 0013).

Slack POSTs button clicks (and other interactions) to a single endpoint as an
``application/x-www-form-urlencoded`` body with a ``payload`` field containing
JSON. We:

    1. verify the Slack request signature (``X-Slack-Signature`` /
       ``X-Slack-Request-Timestamp``) against the configured signing secret;
    2. parse the payload via :meth:`SlackDelivery.handle_callback`;
    3. for a feedback click, recover the tenant from the investigation
       (the click carries the investigation_id as the button ``value``) and
       record the feedback.

This endpoint is *not* bearer-authenticated — Slack can't send our API key —
so the signature is the only authentication. A stale (>5 min) or mismatched
signature is a 401.

We always answer 200 to Slack for *recognised-but-unactionable* clicks (e.g.
the investigation was deleted) so Slack doesn't show the user an error; only a
bad signature or unparseable body is an error status.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sre.api.deps import get_session
from ai_sre.config import get_settings
from ai_sre.core.feedback.repository import FeedbackRepository
from ai_sre.core.feedback.service import FeedbackService
from ai_sre.delivery.slack import SlackDelivery
from ai_sre.models.investigation import Investigation
from ai_sre.utils.logging import get_logger
from ai_sre.utils.webhook_signature import verify_slack_request

router = APIRouter()
logger = get_logger(__name__)

# Stateless parser (handle_callback touches no network/DB); reused per process.
_slack = SlackDelivery()

_BAD_SIGNATURE = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail={
        "code": "auth.invalid_signature",
        "message": "Slack signature verification failed.",
    },
)


@router.post(
    "/delivery/slack/callback",
    name="slack_callback",
    summary="Receive Slack interactive callbacks (feedback buttons).",
)
async def slack_callback(
    request: Request,
    x_slack_signature: str | None = Header(default=None, alias="X-Slack-Signature"),
    x_slack_timestamp: str | None = Header(
        default=None, alias="X-Slack-Request-Timestamp"
    ),
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    raw = await request.body()
    signing_secret = get_settings().slack_signing_secret.get_secret_value()
    if not verify_slack_request(
        signing_secret, x_slack_timestamp, raw, x_slack_signature
    ):
        raise _BAD_SIGNATURE

    parsed = parse_qs(raw.decode("utf-8"))
    payload_values = parsed.get("payload")
    if not payload_values:
        return {"ok": True}
    try:
        payload = json.loads(payload_values[0])
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "slack.bad_payload", "message": "Malformed payload."},
        ) from None

    result = await _slack.handle_callback(payload)
    if result.kind != "feedback":
        return {"ok": True}

    inv_id_raw = result.payload.get("investigation_id")
    try:
        investigation_id = UUID(str(inv_id_raw))
    except (ValueError, TypeError):
        return {"ok": True}

    # Recover the tenant from the investigation (the callback isn't tenant-
    # scoped). Unscoped lookup, like the worker's bootstrap path.
    investigation = await session.get(Investigation, investigation_id)
    if investigation is None:
        # Deleted/unknown — acknowledge so Slack doesn't flag an error.
        logger.info("feedback.callback_unknown_investigation",
                    investigation_id=str(investigation_id))
        return {"ok": True}

    service = FeedbackService(
        FeedbackRepository(session, investigation.tenant_id)
    )
    await service.record(
        investigation_id=investigation_id,
        kind=str(result.payload["feedback_kind"]),
        actor=result.payload.get("actor"),
        metadata={"source": "slack"},
    )
    return {"ok": True}
