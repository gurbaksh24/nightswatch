"""Alert DTOs.

`AlertmanagerPayload` mirrors Alertmanager webhook v4. Kept here so the
webhook receiver in `api/alerts.py` can validate cheaply.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AlertmanagerAlert(BaseModel):
    status: str
    labels: dict[str, str]
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: datetime
    endsAt: datetime | None = None
    generatorURL: str | None = None
    fingerprint: str | None = None


class AlertmanagerPayload(BaseModel):
    version: str
    groupKey: str
    truncatedAlerts: int = 0
    status: str
    receiver: str
    groupLabels: dict[str, str] = Field(default_factory=dict)
    commonLabels: dict[str, str] = Field(default_factory=dict)
    commonAnnotations: dict[str, str] = Field(default_factory=dict)
    externalURL: str | None = None
    alerts: list[AlertmanagerAlert]


class AlertReceiveResponse(BaseModel):
    accepted: int
    alert_ids: list[UUID]


# ---- New Relic webhook (spec 0020) ----

# NR issue priority → our severity label. Unknown values pass through
# lowercased so custom priorities aren't lost.
_NR_PRIORITY_TO_SEVERITY = {
    "CRITICAL": "critical",
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
}

# Issue states we ingest. Anything else (CLOSED, DEACTIVATED, ...) is
# acknowledged but not investigated — workflows often notify on close too.
_NR_OPEN_STATES = frozenset({"ACTIVATED", "CREATED", "OPEN"})


class NewRelicWebhookPayload(BaseModel):
    """Body of ``POST /v1/webhooks/newrelic/{tenant_id}``.

    This is OUR contract, not New Relic's native issue shape: the tenant
    points a NR workflow at the webhook with a *custom payload template*
    that emits exactly these fields (copy-pasteable template in
    ``docs/05-api-spec.md``). Unknown extra fields are ignored.
    """

    issue_id: str = Field(min_length=1)
    title: str = ""
    state: str = "ACTIVATED"
    priority: str | None = None
    condition_name: str = Field(min_length=1)
    policy_name: str | None = None
    entity_names: list[str] = Field(default_factory=list)
    nrql: str | None = None
    route_group: str | None = None
    runbook_url: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    started_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.state.upper() in _NR_OPEN_STATES

    def to_alertmanager(self) -> AlertmanagerPayload:
        """Normalize into the Alertmanager shape so ``AlertService.ingest``
        (fingerprint, dedupe, enqueue) is reused verbatim.

        ``issue_id`` deliberately goes to annotations, not labels — it is
        per-occurrence and would break fingerprint dedupe.
        """
        severity = None
        if self.priority:
            severity = _NR_PRIORITY_TO_SEVERITY.get(self.priority.upper(), self.priority.lower())

        # Template extras first; computed keys win on collision.
        labels: dict[str, str] = dict(self.labels)
        labels["alertname"] = self.condition_name
        if severity:
            labels["severity"] = severity
        if self.policy_name:
            labels["policy"] = self.policy_name
        if self.route_group:
            labels["route_group"] = self.route_group
        if self.entity_names:
            labels["entity"] = self.entity_names[0]

        annotations: dict[str, str] = {"issue_id": self.issue_id}
        if self.title:
            annotations["summary"] = self.title
        if self.nrql:
            annotations["nrql"] = self.nrql
        if self.runbook_url:
            annotations["runbook_url"] = self.runbook_url
        if len(self.entity_names) > 1:
            annotations["other_entities"] = ", ".join(self.entity_names[1:])

        alert = AlertmanagerAlert(
            status="firing",
            labels=labels,
            annotations=annotations,
            startsAt=self.started_at or datetime.now(UTC),
        )
        return AlertmanagerPayload(
            version="4",
            groupKey=f"newrelic:{self.policy_name or ''}:{self.condition_name}",
            status="firing",
            receiver="newrelic-workflow",
            alerts=[alert],
        )
