"""Knowledge-base DTOs (spec 0014)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class KnowledgeDocResponse(BaseModel):
    id: UUID
    kind: str
    title: str
    metadata: dict[str, Any] = {}
    created_at: datetime


class KnowledgeSearchHit(BaseModel):
    doc_id: UUID
    chunk_id: UUID
    text: str
    headings: list[str] = []
    score: float
