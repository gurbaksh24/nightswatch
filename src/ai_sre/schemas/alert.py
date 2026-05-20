"""Alert DTOs.

`AlertmanagerPayload` mirrors Alertmanager webhook v4. Kept here so the
webhook receiver in `api/alerts.py` can validate cheaply.
"""

from __future__ import annotations

from datetime import datetime
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
