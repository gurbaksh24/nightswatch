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
    is_backtest: bool = False
    dry_run: bool = False
    replayed_from: UUID | None = None


class HypothesisOut(BaseModel):
    # Robust to both the LLM report shape and the deterministic fallback.
    statement: str
    confidence: str = "low"
    confirmed: bool | None = None
    evidence: list[dict[str, Any]] = []


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


class StageTrace(BaseModel):
    name: str
    status: str
    attempt: int
    started_at: datetime | None = None
    completed_at: datetime | None = None
    output: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class ToolCallTrace(BaseModel):
    tool_name: str
    input: dict[str, Any]
    output: dict[str, Any] | None = None
    outcome: str
    latency_ms: int
    created_at: datetime


class TraceResponse(BaseModel):
    investigation_id: UUID
    stages: list[StageTrace]
    tool_calls: list[ToolCallTrace]


class BacktestRequest(BaseModel):
    alert_payload: dict[str, Any]
    service_id: UUID
    dry_run: bool = False
