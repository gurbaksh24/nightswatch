"""Feedback DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class FeedbackCreateRequest(BaseModel):
    kind: Literal["useful", "not_useful", "actual_cause", "comment"]
    actor: str | None = None
    body: str | None = None


class FeedbackResponse(BaseModel):
    id: UUID
    kind: str
    actor: str | None = None
    body: str | None = None
    created_at: datetime
