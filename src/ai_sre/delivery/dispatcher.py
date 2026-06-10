"""DeliveryDispatcher — fan a report out to the configured channels.

Boundary-clean: this lives in ``delivery/`` and must not import ``core/``, so
it takes *already-resolved* channel configs (e.g. ``{"slack": {channel_id,
bot_token}}``) rather than looking integrations up itself. The composition
root (the worker) resolves configs from the tenant's integrations and injects
them. Adding a channel is: implement ``DeliveryChannel`` + register it here
(NFR-8.2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ai_sre.delivery.base import DeliveryChannel, DeliveryReceipt
from ai_sre.exceptions import DeliveryError
from ai_sre.utils.logging import get_logger

if TYPE_CHECKING:
    from ai_sre.core.investigation.context import Report

logger = get_logger(__name__)


class DeliveryDispatcher:
    """Routes a report to each channel that has a resolved config."""

    def __init__(self, channels: dict[str, DeliveryChannel]) -> None:
        self.channels = channels

    async def dispatch(
        self, report: Report, configs: dict[str, dict[str, Any]]
    ) -> list[DeliveryReceipt]:
        """Deliver ``report`` to every channel present in both ``configs`` and
        the registered channels. Never raises — a channel failure becomes a
        failed receipt so one bad channel doesn't sink the others."""
        receipts: list[DeliveryReceipt] = []
        for kind, config in configs.items():
            channel = self.channels.get(kind)
            if channel is None:
                continue
            try:
                receipts.append(await channel.deliver(report, config))
            except DeliveryError as exc:
                logger.warning("delivery.failed", channel=kind, error=str(exc))
                receipts.append(
                    DeliveryReceipt(success=False, channel=kind, error=str(exc))
                )
        return receipts
