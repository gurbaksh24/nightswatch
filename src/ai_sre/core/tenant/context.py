"""TenantContext: the authenticated tenant for the duration of one request.

Lives in `core/` so that core services (e.g. `ApiKeyService`) can return it
without importing from `api/`. `api/deps.py` re-exports it for backward
compatibility with route signatures that still type-annotate against
`api.deps.TenantContext`.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class TenantContext:
    """The authenticated tenant identity carried through a request or job."""

    tenant_id: UUID
    name: str
    slug: str
    api_key_id: UUID
