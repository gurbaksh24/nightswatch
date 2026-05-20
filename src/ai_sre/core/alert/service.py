"""Alert ingestion service.

Receives a parsed Alertmanager payload, persists each alert, computes a
fingerprint, runs deduplication, and decides whether to:

    (a) create a new investigation, or
    (b) link the alert to an existing investigation that's still in the
        dedupe window.

In either case, enqueue work onto the job queue. Webhook latency must remain
< 500 ms p95, so this service is strictly persistence + enqueue — no LLM,
no outbound HTTP.

LLD §6 has the algorithm.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from ai_sre.api.deps import TenantContext
    from ai_sre.core.alert.deduplicator import Deduplicator


@dataclass(frozen=True)
class IngestResult:
    alert_ids: list[UUID]
    new_investigation_ids: list[UUID]
    linked_investigation_ids: list[UUID]


class AlertService:
    """Public surface for the alert ingestion pipeline."""

    def __init__(self, deduplicator: Deduplicator) -> None:
        self.deduplicator = deduplicator

    async def ingest(
        self,
        tenant: TenantContext,
        payload: dict,          # AlertmanagerPayload Pydantic model
        raw: bytes,
    ) -> IngestResult:
        """Persist alerts and decide on investigation linkage.

        Algorithm:
            1. For each alert in payload.alerts:
                 a. Compute fingerprint = sha256(tenant_id || alert_name ||
                    sorted(stable_labels) || severity).
                 b. Persist alert row (raw + normalised).
                 c. Look up an existing investigation with matching fingerprint
                    started within dedupe window (default 15 min).
                 d. If found → link (update alert_count / last_seen_at).
                 e. Else → create new Investigation(PENDING), enqueue.
            2. Return aggregated IngestResult.
        """
        # TODO(spec-NNNN: alert-ingestion)
        raise NotImplementedError
