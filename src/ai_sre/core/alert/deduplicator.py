"""Alert fingerprinting and dedupe window lookups.

Fingerprint formula:

    sha256(
        tenant_id || alert_name || sorted(labels excluding `unstable_keys`) || severity
    )

`unstable_keys` defaults to {"instance", "pod", "node", "container_id"} and is
configurable per tenant. The intent: two firings of "HighErrorRate" on
different pods of the same service are the same incident, not two.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_DEFAULT_UNSTABLE_KEYS: frozenset[str] = frozenset(
    {"instance", "pod", "node", "container_id", "replica", "host"}
)


@dataclass(frozen=True)
class Fingerprint:
    value: str  # hex digest


@dataclass
class DedupeConfig:
    window: timedelta = timedelta(minutes=15)
    unstable_keys: frozenset[str] = field(default_factory=lambda: _DEFAULT_UNSTABLE_KEYS)


def compute_fingerprint(
    *,
    tenant_id: UUID,
    alert_name: str,
    labels: dict[str, str],
    severity: str | None,
    unstable_keys: frozenset[str] = _DEFAULT_UNSTABLE_KEYS,
) -> Fingerprint:
    """Stable, deterministic alert fingerprint. Pure function — testable."""
    stable = {k: v for k, v in labels.items() if k not in unstable_keys}
    canonical = json.dumps(
        {
            "tenant": str(tenant_id),
            "name": alert_name,
            "labels": dict(sorted(stable.items())),
            "severity": severity or "",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return Fingerprint(value=digest)


class Deduplicator:
    """Lookup-and-link logic over the alerts/investigations tables."""

    def __init__(self, session: AsyncSession, config: DedupeConfig) -> None:
        self.session = session
        self.config = config

    async def find_active_investigation(
        self,
        *,
        tenant_id: UUID,
        fingerprint: Fingerprint,
        as_of: datetime,
    ) -> UUID | None:
        """Return an existing investigation id if one is within the window."""
        # TODO(spec-NNNN: alert-ingestion)
        raise NotImplementedError
