"""Alert ingestion service.

Receives a parsed Alertmanager payload and, for each alert: computes a
fingerprint, persists the `alert` row, runs dedupe, creates an
`investigation` (PENDING) when needed, and enqueues `run_investigation`.

This is the synchronous hot path behind the webhook (target p95 < 500ms), so
it does only persistence + enqueue — no LLM, no outbound HTTP.

See LLD §6 and spec 0006.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from ai_sre.core.alert.fingerprinter import DEFAULT_UNSTABLE_LABELS, fingerprint
from ai_sre.core.alert.repository import AlertRepository
from ai_sre.core.investigation.repository import InvestigationRepository
from ai_sre.core.service.repository import ServiceRepository
from ai_sre.exceptions import AISREError
from ai_sre.queue.base import JobQueue
from ai_sre.schemas.alert import AlertmanagerPayload
from ai_sre.utils.logging import get_logger

logger = get_logger(__name__)


class AlertValidationError(AISREError):
    """An alert in the payload is malformed (e.g. missing alertname)."""

    code = "validation.failed"


@dataclass(frozen=True)
class IngestResult:
    """Outcome of ingesting one payload."""

    alert_ids: list[UUID]
    new_investigation_ids: list[UUID]
    linked_investigation_ids: list[UUID]


class AlertService:
    """Persist + dedupe + enqueue. Tenant id is passed per call (the webhook
    has no bearer/tenant context — it's scoped by URL + signature)."""

    def __init__(
        self,
        alert_repo: AlertRepository,
        investigation_repo: InvestigationRepository,
        service_repo: ServiceRepository,
        job_queue: JobQueue,
        *,
        dedupe_window_seconds: int,
        unstable_labels: Iterable[str] = DEFAULT_UNSTABLE_LABELS,
    ) -> None:
        self.alert_repo = alert_repo
        self.investigation_repo = investigation_repo
        self.service_repo = service_repo
        self.job_queue = job_queue
        self.dedupe_window_seconds = dedupe_window_seconds
        self.unstable_labels = tuple(unstable_labels)

    async def ingest(
        self,
        tenant_id: UUID,
        payload: AlertmanagerPayload,
        raw: bytes,
    ) -> IngestResult:
        """Persist every alert, dedupe by fingerprint, enqueue investigations.

        Raises:
            AlertValidationError: if any alert lacks ``labels.alertname``
                (the whole payload is rejected before anything is persisted).
        """
        # Validate up front so we never half-ingest a bad payload.
        for incoming in payload.alerts:
            if not incoming.labels.get("alertname"):
                raise AlertValidationError(
                    "Alert is missing required label 'alertname'."
                )

        # The subject service (one per tenant) anchors every investigation.
        # If the tenant hasn't onboarded one yet we still persist the alert
        # for audit, but can't investigate it.
        service = await self.service_repo.get_for_tenant()

        now = datetime.now(UTC)
        window_start = now - timedelta(seconds=self.dedupe_window_seconds)

        alert_ids: list[UUID] = []
        new_inv: list[UUID] = []
        linked_inv: list[UUID] = []

        for am_alert in payload.alerts:
            name = am_alert.labels["alertname"]
            severity = am_alert.labels.get("severity")
            fp = fingerprint(tenant_id, name, am_alert.labels, severity, self.unstable_labels)

            alert = await self.alert_repo.create(
                source="alertmanager",
                received_at=now,
                raw_payload=am_alert.model_dump(mode="json"),
                alert_name=name,
                severity=severity,
                status=am_alert.status,
                started_at=am_alert.startsAt,
                ended_at=am_alert.endsAt,
                labels=am_alert.labels,
                annotations=am_alert.annotations,
                fingerprint=fp,
            )
            alert_ids.append(alert.id)
            log = logger.bind(
                tenant_id=str(tenant_id), alert_id=str(alert.id), fingerprint=fp
            )

            if service is None:
                log.warning("alert.ingested_without_service")
                continue

            existing = await self.investigation_repo.find_active_by_fingerprint(
                fp, since=window_start
            )
            if existing is not None:
                await self.alert_repo.set_investigation(alert, existing.id)
                linked_inv.append(existing.id)
                log.info("alert.linked_to_investigation", investigation_id=str(existing.id))
                continue

            investigation = await self.investigation_repo.create_pending(
                service_id=service.id,
                triggering_alert_id=alert.id,
                fingerprint=fp,
            )
            await self.alert_repo.set_investigation(alert, investigation.id)
            await self.job_queue.enqueue(
                "investigation", {"investigation_id": str(investigation.id)}
            )
            new_inv.append(investigation.id)
            log.info("alert.investigation_created", investigation_id=str(investigation.id))

        return IngestResult(
            alert_ids=alert_ids,
            new_investigation_ids=new_inv,
            linked_investigation_ids=linked_inv,
        )
