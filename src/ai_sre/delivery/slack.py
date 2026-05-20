"""Slack delivery channel.

Posts the report as a Block Kit message. Handles button callbacks for the
feedback loop.

See LLD §14 for the message layout.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import httpx

from ai_sre.delivery.base import CallbackResult, DeliveryChannel, DeliveryReceipt
from ai_sre.exceptions import DeliveryError

if TYPE_CHECKING:
    from ai_sre.core.investigation.context import Report


class SlackDelivery(DeliveryChannel):
    name = "slack"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=10)

    async def deliver(self, report: Report, channel_config: dict) -> DeliveryReceipt:
        """Post the report to Slack.

        `channel_config` carries the tenant's Slack bot token and channel ID.
        """
        try:
            blocks = self._build_blocks(report)
            payload = {
                "channel": channel_config["channel_id"],
                "blocks": blocks,
                "text": report.headline or "AI-SRE diagnosis",
            }
            token = channel_config["bot_token"]
            r = await self._client.post(
                "https://slack.com/api/chat.postMessage",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            data = r.json()
            if not data.get("ok"):
                raise DeliveryError(f"Slack error: {data.get('error')}", details=data)
            return DeliveryReceipt(
                success=True,
                channel=self.name,
                delivered_at=datetime.now(timezone.utc),
                external_id=data.get("ts"),
            )
        except httpx.HTTPError as e:
            raise DeliveryError(f"Slack HTTP error: {e}") from e

    async def handle_callback(self, payload: dict) -> CallbackResult:
        """Translate a Slack interactive payload into a feedback event.

        Slack sends a JSON-encoded `payload` form field for interactive
        components. The button `action_id` carries the feedback kind.
        """
        # TODO(spec-NNNN: slack-callback):
        #   1. Parse payload (already JSON-decoded by the route).
        #   2. Verify the user / channel / message ts.
        #   3. Map action_id → feedback kind.
        #   4. Return CallbackResult(kind="feedback", payload={...}).
        return CallbackResult(kind="ignore", payload={})

    # ---- block builders ----

    def _build_blocks(self, report: Report) -> list[dict[str, Any]]:
        """Block Kit JSON for the report.

        The structure stays under Slack's limits (50 blocks, 3000 chars/text)
        even for verbose reports — long fields are truncated.
        """
        # TODO(spec-NNNN: slack-block-kit):
        #   header / section / divider / hypothesis sections / actions
        return [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": (report.headline or "AI-SRE")[:150]},
            }
        ]
