"""API key DTOs."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # Admin-auth bootstrap (spec 0018): the tenant to issue the key for.
    # Required when authenticating with the admin token; with tenant-key auth
    # it must be absent or equal to the caller's own tenant id.
    tenant_id: UUID | None = None


class ApiKeyIssuedResponse(BaseModel):
    """Returned only once, at creation. The `key` is never shown again."""

    id: UUID
    key: str
    prefix: str
    created_at: datetime


class ApiKeyResponse(BaseModel):
    id: UUID
    name: str
    prefix: str
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
