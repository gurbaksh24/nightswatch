"""Investigation DTOs (read-only views)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class InvestigationSummary(BaseModel):
    id: UUID
    service_id: UUID
    status: str
    confidence: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    fingerprint: str


class HypothesisOut(BaseModel):
    statement: str
    confidence: str
    evidence: list[dict[str, Any]]


class ReportResponse(BaseModel):
    schema_version: str
    headline: str
    confidence: str
    hypotheses: list[HypothesisOut]
    next_actions: list[dict[str, Any]]
    related_incidents: list[dict[str, Any]]
    delivered_at: datetime | None = None


class InvestigationDetail(InvestigationSummary):
    report: ReportResponse | None = None


class BacktestRequest(BaseModel):
    alert_payload: dict[str, Any]
    service_id: UUID
    dry_run: bool = False
