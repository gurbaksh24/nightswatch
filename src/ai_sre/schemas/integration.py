"""Integration DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class PromAuthBearer(BaseModel):
    type: Literal["bearer"] = "bearer"
    token: str


class PromAuthBasic(BaseModel):
    type: Literal["basic"] = "basic"
    username: str
    password: str


class PromAuthNone(BaseModel):
    type: Literal["none"] = "none"


class PrometheusConfigRequest(BaseModel):
    url: str
    auth: PromAuthBearer | PromAuthBasic | PromAuthNone = PromAuthNone()


class IntegrationCreateRequest(BaseModel):
    kind: Literal["prometheus", "slack"]
    name: str
    config: dict[str, Any]


class IntegrationResponse(BaseModel):
    id: UUID
    kind: str
    name: str
    status: str
    config_public: dict[str, Any]
    last_health_check_at: datetime | None = None
    created_at: datetime
