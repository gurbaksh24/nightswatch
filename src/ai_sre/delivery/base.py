"""DeliveryChannel ABC.

The investigation orchestrator hands a final Report to a DeliveryDispatcher,
which routes to the appropriate channel implementation per tenant config.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ai_sre.core.investigation.context import Report


@dataclass(frozen=True)
class DeliveryReceipt:
    success: bool
    channel: str
    delivered_at: datetime | None = None
    external_id: str | None = None     # e.g. Slack ts
    error: str | None = None


@dataclass(frozen=True)
class CallbackResult:
    """Result of handling a channel-originated interaction (e.g. button click)."""

    kind: str                          # "feedback" | "ignore"
    payload: dict[str, Any]


class DeliveryChannel(ABC):
    name: str

    @abstractmethod
    async def deliver(self, report: Report, channel_config: dict) -> DeliveryReceipt: ...

    @abstractmethod
    async def handle_callback(self, payload: dict) -> CallbackResult: ...
