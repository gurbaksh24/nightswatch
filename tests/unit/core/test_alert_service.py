"""Unit tests for :class:`AlertService` (spec 0006).

In-memory fakes for the repos + queue. The fake investigation repo mirrors
the real dedupe semantics (active statuses + window) so the service's
link-vs-create branching is exercised faithfully.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, NamedTuple
from uuid import UUID

import pytest

from ai_sre.core.alert.service import AlertService, AlertValidationError
from ai_sre.core.investigation.repository import ACTIVE_DEDUPE_STATUSES
from ai_sre.models.alert import Alert
from ai_sre.models.investigation import Investigation
from ai_sre.models.service import Service
from ai_sre.schemas.alert import AlertmanagerAlert, AlertmanagerPayload
from ai_sre.utils.ids import new_id

TENANT = new_id()


# ---- fakes ----


class _FakeAlertRepo:
    def __init__(self) -> None:
        self.rows: list[Alert] = []

    async def create(self, **kw: Any) -> Alert:
        row = Alert(id=new_id(), tenant_id=TENANT, investigation_id=None, **kw)
        self.rows.append(row)
        return row

    async def set_investigation(self, alert: Alert, investigation_id: UUID) -> None:
        alert.investigation_id = investigation_id


class _FakeInvestigationRepo:
    """Faithful enough to test dedupe: filters fingerprint + active status +
    created-at window, exactly like the real repository."""

    def __init__(self) -> None:
        self.rows: list[Investigation] = []

    async def find_active_by_fingerprint(
        self, fingerprint: str, *, since: datetime
    ) -> Investigation | None:
        matches = [
            r
            for r in self.rows
            if r.fingerprint == fingerprint
            and r.status in ACTIVE_DEDUPE_STATUSES
            and r.created_at >= since
        ]
        return sorted(matches, key=lambda r: r.created_at)[-1] if matches else None

    async def create_pending(
        self, *, service_id: UUID, triggering_alert_id: UUID, fingerprint: str
    ) -> Investigation:
        row = Investigation(
            id=new_id(),
            tenant_id=TENANT,
            service_id=service_id,
            triggering_alert_id=triggering_alert_id,
            fingerprint=fingerprint,
            status="pending",
            created_at=datetime.now(UTC),
        )
        self.rows.append(row)
        return row


class _FakeServiceRepo:
    def __init__(self, service: Service | None) -> None:
        self._service = service

    async def get_for_tenant(self) -> Service | None:
        return self._service


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def enqueue(self, kind: str, payload: dict[str, Any], *, delay_seconds: int = 0) -> str:
        self.calls.append((kind, payload))
        return f"job-{len(self.calls)}"

    async def cancel(self, job_id: str) -> None:
        return None


class _Built(NamedTuple):
    service: AlertService
    queue: _FakeQueue
    inv: _FakeInvestigationRepo
    alerts: _FakeAlertRepo


def _service_row() -> Service:
    return Service(id=new_id(), tenant_id=TENANT, name="checkout", label_selector={"app": "c"})


def _build(*, service: Service | None) -> _Built:
    queue = _FakeQueue()
    inv = _FakeInvestigationRepo()
    alerts = _FakeAlertRepo()
    svc = AlertService(
        alerts,  # type: ignore[arg-type]
        inv,  # type: ignore[arg-type]
        _FakeServiceRepo(service),  # type: ignore[arg-type]
        queue,
        dedupe_window_seconds=900,
    )
    return _Built(svc, queue, inv, alerts)


def _alert(
    name: str = "HighErrorRate",
    *,
    severity: str | None = "critical",
    status: str = "firing",
    **extra: str,
) -> AlertmanagerAlert:
    labels = {"alertname": name}
    if severity is not None:
        labels["severity"] = severity
    labels.update(extra)
    return AlertmanagerAlert(
        status=status, labels=labels, annotations={}, startsAt=datetime.now(UTC)
    )


def _payload(*alerts: AlertmanagerAlert) -> AlertmanagerPayload:
    return AlertmanagerPayload(
        version="4", groupKey="g", status="firing", receiver="r", alerts=list(alerts)
    )


# ---- tests ----


@pytest.mark.unit
@pytest.mark.asyncio
async def test_new_alert_creates_investigation_and_enqueues() -> None:
    b = _build(service=_service_row())
    result = await b.service.ingest(TENANT, _payload(_alert()), b"{}")

    assert len(result.alert_ids) == 1
    assert len(result.new_investigation_ids) == 1
    assert result.linked_investigation_ids == []
    assert b.queue.calls == [
        ("investigation", {"investigation_id": str(result.new_investigation_ids[0])})
    ]
    assert b.alerts.rows[0].investigation_id == result.new_investigation_ids[0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_duplicate_within_window_links_no_new_investigation() -> None:
    b = _build(service=_service_row())
    first = await b.service.ingest(TENANT, _payload(_alert()), b"{}")
    # unstable label (pod) differs → same fingerprint → should link.
    second = await b.service.ingest(TENANT, _payload(_alert(pod="pod-2")), b"{}")

    assert second.new_investigation_ids == []
    assert second.linked_investigation_ids == first.new_investigation_ids
    assert len(b.inv.rows) == 1
    assert len(b.queue.calls) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_investigation_does_not_dedupe() -> None:
    b = _build(service=_service_row())
    first = await b.service.ingest(TENANT, _payload(_alert()), b"{}")
    b.inv.rows[0].status = "failed"  # repeat should start fresh

    second = await b.service.ingest(TENANT, _payload(_alert()), b"{}")
    assert len(second.new_investigation_ids) == 1
    assert second.new_investigation_ids != first.new_investigation_ids
    assert len(b.queue.calls) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_multiple_alerts_same_fingerprint_one_investigation() -> None:
    b = _build(service=_service_row())
    result = await b.service.ingest(TENANT, _payload(_alert(pod="a"), _alert(pod="b")), b"{}")
    assert len(result.alert_ids) == 2
    assert len(b.inv.rows) == 1
    assert len(b.queue.calls) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_alertname_rejected_before_persist() -> None:
    b = _build(service=_service_row())
    bad = AlertmanagerAlert(
        status="firing", labels={"severity": "warning"}, annotations={}, startsAt=datetime.now(UTC)
    )
    with pytest.raises(AlertValidationError):
        await b.service.ingest(TENANT, _payload(bad), b"{}")
    assert b.alerts.rows == []
    assert b.queue.calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_service_persists_alert_but_skips_investigation() -> None:
    b = _build(service=None)
    result = await b.service.ingest(TENANT, _payload(_alert()), b"{}")
    assert len(result.alert_ids) == 1
    assert result.new_investigation_ids == []
    assert b.inv.rows == []
    assert b.queue.calls == []
