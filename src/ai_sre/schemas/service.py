"""Service / topology / metric catalog DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ServiceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    label_selector: dict[str, str]
    ownership: dict[str, Any] | None = None


class ServiceResponse(BaseModel):
    id: UUID
    name: str
    label_selector: dict[str, str]
    ownership: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class DependencyResponse(BaseModel):
    id: UUID
    direction: str
    name: str
    confirmed_by_user: bool


class TopologyResponse(BaseModel):
    upstream: list[DependencyResponse]
    downstream: list[DependencyResponse]


class MetricEntryResponse(BaseModel):
    metric_name: str
    metric_type: str
    labels: dict[str, Any]
    unit: str | None = None
