"""Integration DTOs.

Wire format for the integrations API. The kind discriminator currently has
only ``prometheus`` — Slack integrations are created via the OAuth flow in
spec 0010 and don't go through ``POST /v1/integrations``.

The ``config`` field is typed (``PrometheusConfigRequest``) so FastAPI's
automatic Pydantic validation returns 422 on malformed payloads (per
spec 0002 edge cases). Adding a new kind = adding a new discriminated
variant.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PromAuthBearer(BaseModel):
    """Bearer-token auth for a Prometheus integration."""

    type: Literal["bearer"] = "bearer"
    token: str = Field(min_length=1)


class PromAuthBasic(BaseModel):
    """HTTP Basic auth for a Prometheus integration."""

    type: Literal["basic"] = "basic"
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class PromAuthNone(BaseModel):
    """No auth (Prometheus is open or behind network policy)."""

    type: Literal["none"] = "none"


PromAuth = Annotated[
    PromAuthBearer | PromAuthBasic | PromAuthNone,
    Field(discriminator="type"),
]


class PrometheusConfigRequest(BaseModel):
    """Wire-format config block for a Prometheus integration."""

    url: str = Field(min_length=1, max_length=2048)
    auth: PromAuth = PromAuthNone()


class IntegrationCreateRequest(BaseModel):
    """Request body for ``POST /v1/integrations``.

    Currently only ``kind="prometheus"`` is accepted; Slack uses the OAuth
    flow. Unknown kinds are rejected at the validation layer as 422.
    """

    kind: Literal["prometheus"]
    name: str = Field(min_length=1, max_length=255)
    config: PrometheusConfigRequest


class IntegrationResponse(BaseModel):
    """Response shape — never includes encrypted blobs or secrets.

    ``config_public`` is whatever the service chose to expose (e.g. URL
    host, auth type). Tokens, passwords, and the webhook signing secret
    never leave this layer.
    """

    id: UUID
    kind: str
    name: str
    status: str
    config_public: dict[str, Any]
    last_health_check_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class IntegrationCreatedResponse(IntegrationResponse):
    """Response for ``POST /v1/integrations``.

    For a Prometheus integration this carries the freshly generated webhook
    signing secret — returned **once**, never again (configure your
    Alertmanager with it). ``None`` for kinds that don't use one.
    """

    webhook_signing_secret: str | None = Field(
        default=None,
        description="Webhook signing secret. Shown once at creation only.",
    )


class WebhookSecretResponse(BaseModel):
    """Response for ``POST /v1/integrations/{id}/webhook-secret`` (rotate)."""

    integration_id: UUID
    webhook_signing_secret: str = Field(description="Shown once; rotates the old one.")
