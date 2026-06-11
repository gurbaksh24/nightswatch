"""BacktestService — re-run a (historical) alert and replay investigations.

Backtest is the onboarding tool (FR-10.3): submit a recorded Alertmanager
payload and a service, and see what the pipeline produces *now*. Unlike the
live webhook path (``AlertService.ingest``) there's no dedup and the subject
service is given explicitly — it's a deliberate, on-demand run.

``dry_run`` marks the investigation so the orchestrator skips delivery (no
Slack post). Replay duplicates an existing investigation as a fresh run linked
via ``replayed_from``.

Tenant-scoped via its repositories.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from ai_sre.core.alert.fingerprinter import DEFAULT_UNSTABLE_LABELS, fingerprint
from ai_sre.core.alert.repository import AlertRepository
from ai_sre.core.alert.service import AlertValidationError
from ai_sre.core.investigation.repository import InvestigationRepository
from ai_sre.core.service.repository import ServiceRepository
from ai_sre.exceptions import ServiceNotFound
from ai_sre.models.investigation import Investigation
from ai_sre.queue.base import JobQueue
from ai_sre.schemas.alert import AlertmanagerPayload
from ai_sre.utils.logging import get_logger

logger = get_logger(__name__)


class BacktestService:
    """Create + enqueue backtest and replay investigations for a tenant."""

    def __init__(
        self,
        alert_repo: AlertRepository,
        investigation_repo: InvestigationRepository,
        service_repo: ServiceRepository,
        job_queue: JobQueue,
        *,
        unstable_labels: Iterable[str] = DEFAULT_UNSTABLE_LABELS,
    ) -> None:
        self.alert_repo = alert_repo
        self.investigation_repo = investigation_repo
        self.service_repo = service_repo
        self.job_queue = job_queue
        self.unstable_labels = tuple(unstable_labels)
        self.tenant_id = investigation_repo.tenant_id

    async def backtest(
        self,
        *,
        payload: AlertmanagerPayload,
        service_id: UUID,
        dry_run: bool,
    ) -> Investigation:
        """Persist the first alert in ``payload`` and run it against ``service_id``.

        Raises:
            ServiceNotFound: the service isn't owned by this tenant.
            AlertValidationError: the payload has no alerts, or the first alert
                is missing ``labels.alertname``.
        """
        service = await self.service_repo.get_by_id(service_id)
        if service is None:
            raise ServiceNotFound(f"Service {service_id} not found for this tenant.")
        if not payload.alerts:
            raise AlertValidationError("Backtest payload contains no alerts.")

        am_alert = payload.alerts[0]
        name = am_alert.labels.get("alertname")
        if not name:
            raise AlertValidationError("Alert is missing required label 'alertname'.")
        severity = am_alert.labels.get("severity")
        fp = fingerprint(
            self.tenant_id, name, am_alert.labels, severity, self.unstable_labels
        )

        now = datetime.now(UTC)
        alert = await self.alert_repo.create(
            source="backtest",
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
        investigation = await self.investigation_repo.create_pending(
            service_id=service.id,
            triggering_alert_id=alert.id,
            fingerprint=fp,
            is_backtest=True,
            dry_run=dry_run,
        )
        await self.alert_repo.set_investigation(alert, investigation.id)
        await self.job_queue.enqueue(
            "investigation", {"investigation_id": str(investigation.id)}
        )
        logger.info(
            "investigation.backtest_created",
            kind="backtest",
            tenant_id=str(self.tenant_id),
            investigation_id=str(investigation.id),
            dry_run=dry_run,
        )
        return investigation

    async def replay(self, *, investigation_id: UUID) -> Investigation | None:
        """Duplicate an existing investigation as a new run (``replayed_from``).

        Works for any prior status (including ``failed``). Returns ``None`` if
        the source investigation isn't owned by this tenant.
        """
        source = await self.investigation_repo.get(investigation_id)
        if source is None:
            return None

        investigation = await self.investigation_repo.create_pending(
            service_id=source.service_id,
            triggering_alert_id=source.triggering_alert_id,
            fingerprint=source.fingerprint,
            is_backtest=source.is_backtest,
            dry_run=source.dry_run,
            replayed_from=source.id,
        )
        await self.job_queue.enqueue(
            "investigation", {"investigation_id": str(investigation.id)}
        )
        logger.info(
            "investigation.replay_created",
            kind="replay",
            tenant_id=str(self.tenant_id),
            investigation_id=str(investigation.id),
            replayed_from=str(source.id),
        )
        return investigation
