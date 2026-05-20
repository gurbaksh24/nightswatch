"""API key issuance & verification.

Implementation tracked by spec 0001. Contract:

- `issue` returns the plaintext key ONCE (never persisted).
- `verify` returns a `TenantContext` or None; constant-time by virtue of
  hash lookup.
- `list` and `revoke` are tenant-scoped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from ai_sre.api.deps import TenantContext
from ai_sre.core.tenant.repository import ApiKeyRepository, TenantRepository
from ai_sre.utils.crypto import hash_api_key
from ai_sre.utils.ids import generate_api_key


@dataclass(frozen=True)
class IssuedApiKey:
    """Returned to the caller exactly once on issuance."""

    id: UUID
    key: str          # plaintext — show once, never persist
    prefix: str
    created_at: datetime


class ApiKeyService:
    def __init__(
        self,
        api_key_repo: ApiKeyRepository,
        tenant_repo: TenantRepository,
    ) -> None:
        self.api_key_repo = api_key_repo
        self.tenant_repo = tenant_repo

    async def issue(self, tenant_id: UUID, *, name: str) -> IssuedApiKey:
        """Generate, hash-and-store, return the plaintext key once."""
        full, prefix = generate_api_key()
        key_hash = hash_api_key(full)
        _ = await self.api_key_repo.create(
            tenant_id=tenant_id, name=name, key_hash=key_hash, prefix=prefix
        )
        # TODO(spec-0001): return real ID/created_at from the persisted row.
        raise NotImplementedError

    async def verify(self, raw_key: str) -> TenantContext | None:
        """Verify a bearer token and return its tenant context, or None.

        Steps:
            1. Hash the key.
            2. Lookup the active row.
            3. Load the tenant.
            4. Update `last_used_at` (fire-and-forget).
        """
        key_hash = hash_api_key(raw_key)
        _row = await self.api_key_repo.find_by_hash(key_hash)
        # TODO(spec-0001): assemble TenantContext from the row + tenant.
        raise NotImplementedError

    async def list(self, tenant_id: UUID) -> list[object]:
        return await self.api_key_repo.list_for_tenant(tenant_id)

    async def revoke(self, tenant_id: UUID, key_id: UUID) -> None:
        await self.api_key_repo.revoke(tenant_id, key_id)
