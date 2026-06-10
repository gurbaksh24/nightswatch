"""Slack delivery channel — posts the report as a Block Kit message.

Uses httpx directly against the Slack Web API (chat.postMessage). Button
callbacks (handle_callback) are wired in spec 0013; here they're inert.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from ai_sre.delivery.base import CallbackResult, DeliveryChannel, DeliveryReceipt
from ai_sre.delivery.slack.block_kit import build_blocks
from ai_sre.exceptions import DeliveryError

if TYPE_CHECKING:
    from ai_sre.core.investigation.context import Report

_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"


class SlackDelivery(DeliveryChannel):
    name = "slack"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=10)

    async def deliver(
        self, report: Report, channel_config: dict[str, Any]
    ) -> DeliveryReceipt:
        """Post the report to Slack. ``channel_config`` carries the tenant's
        bot token + channel id (resolved by the caller from the integration)."""
        blocks = build_blocks(report)
        payload = {
            "channel": channel_config["channel_id"],
            "blocks": blocks,
            "text": report.headline or "AI-SRE diagnosis",
        }
        try:
            r = await self._client.post(
                _POST_MESSAGE_URL,
                json=payload,
                headers={"Authorization": f"Bearer {channel_config['bot_token']}"},
            )
        except httpx.HTTPError as exc:
            raise DeliveryError(f"Slack HTTP error: {exc}") from exc

        data = r.json()
        if not data.get("ok"):
            raise DeliveryError(
                f"Slack error: {data.get('error')}", details={"slack_error": data.get("error")}
            )
        return DeliveryReceipt(
            success=True,
            channel=self.name,
            delivered_at=datetime.now(UTC),
            external_id=data.get("ts"),
        )

    async def handle_callback(self, payload: dict[str, Any]) -> CallbackResult:
        """Interactive callbacks land in spec 0013; inert for now."""
        return CallbackResult(kind="ignore", payload={})
