"""Service / topology / metric catalog DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ServiceCreateRequest(BaseModel):
    """Body for ``POST /v1/services``.

    ``label_selector`` is treated as immutable post-registration; if it
    needs to change, the service is deleted and re-registered (spec 0004).
    """

    name: str = Field(min_length=1, max_length=255)
    label_selector: dict[str, str] = Field(
        ...,
        description=(
            "Label selector used to identify time-series belonging to this "
            "service in Prometheus, e.g. {'app': 'checkout', 'env': 'prod'}."
        ),
        min_length=1,
    )
    ownership: dict[str, Any] | None = Field(
        default=None,
        description="Optional ownership metadata (team, slack channel, …).",
    )


class ServiceResponse(BaseModel):
    """Public view of a subject service.

    ``warnings`` is populated on ``POST`` (e.g. "label_selector matched 0
    series") and empty on ``GET`` — warnings are about the registration
    event itself, not durable state. ``validation_pending`` is durable, set
    when registration happened before a Prometheus integration existed.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    label_selector: dict[str, str]
    ownership: dict[str, Any] | None = None
    validation_pending: bool = False
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class LabelSelectorValidation(BaseModel):
    """Outcome of validating a label selector against the connected Prometheus.

    Returned by :meth:`ai_sre.core.service.service.ServiceService.validate_label_selector`.
    """

    has_prometheus: bool = Field(
        ...,
        description="True if a Prometheus integration exists for the tenant.",
    )
    series_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of time series matching the selector. 0 when no Prometheus "
            "integration is connected, or when the query succeeded but matched "
            "nothing."
        ),
    )
    error: str | None = Field(
        default=None,
        description=(
            "Set when the validation call to Prometheus failed (auth/network/"
            "etc). The service is still registered; the user can re-validate "
            "after fixing the integration."
        ),
    )


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


class DependencyConfirmation(BaseModel):
    """A single edit in ``PATCH /v1/services/{id}/topology``."""

    dependency_id: UUID
    confirmed_by_user: bool


class TopologyUpdateRequest(BaseModel):
    """Body for ``PATCH /v1/services/{id}/topology``.

    Confirm (or un-confirm) discovered dependency edges by id.
    """

    edits: list[DependencyConfirmation] = Field(min_length=1)
