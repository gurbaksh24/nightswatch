"""API key issuance & verification.

Contract:
    * `issue` returns the plaintext key exactly once. After that, only the
      SHA-256 hash is persisted; the plaintext is unrecoverable.
    * `verify` returns a `TenantContext` for valid, non-expired, non-revoked
      keys, or `None` otherwise. The hash lookup is constant-time-ish by
      virtue of being a direct DB query keyed by hash; there is no
      string-equality compare against an attacker-supplied value.
    * `list` and `revoke` are tenant-scoped.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from ai_sre.core.tenant.context import TenantContext
from ai_sre.core.tenant.repository import ApiKeyRepository, TenantRepository
from ai_sre.models.api_key import ApiKey
from ai_sre.utils.crypto import hash_api_key
from ai_sre.utils.ids import generate_api_key
from ai_sre.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class IssuedApiKey:
    """Returned to the caller exactly once on issuance."""

    id: UUID
    key: str  # plaintext — show once, never persist
    prefix: str
    created_at: datetime


class ApiKeyService:
    """Issue, verify, list, and revoke API keys."""

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
        row = await self.api_key_repo.create(
            tenant_id=tenant_id, name=name, key_hash=key_hash, prefix=prefix
        )
        logger.info(
            "api_key.issued",
            tenant_id=str(tenant_id),
            api_key_id=str(row.id),
            prefix=prefix,
        )
        return IssuedApiKey(
            id=row.id,
            key=full,
            prefix=row.prefix,
            created_at=row.created_at,
        )

    async def verify(self, raw_key: str) -> TenantContext | None:
        """Verify a bearer token and return its tenant context, or `None`.

        Steps:
            1. Hash the key (constant-time SHA-256).
            2. Look up the row by hash.
            3. Reject if revoked or expired.
            4. Load the tenant; reject if missing or not active.
            5. Best-effort update `last_used_at`.
        """
        key_hash = hash_api_key(raw_key)
        row = await self.api_key_repo.find_by_hash(key_hash)
        if row is None:
            return None
        if row.revoked_at is not None:
            return None
        if row.expires_at is not None and row.expires_at <= datetime.now(timezone.utc):
            return None
        tenant = await self.tenant_repo.get(row.tenant_id)
        if tenant is None or tenant.status != "active":
            return None
        await self.api_key_repo.mark_used(row.id)
        return TenantContext(
            tenant_id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            api_key_id=row.id,
        )

    async def list(self, tenant_id: UUID) -> Sequence[ApiKey]:
        """List API keys for a tenant (without secrets)."""
        return await self.api_key_repo.list_for_tenant(tenant_id)

    async def revoke(self, tenant_id: UUID, key_id: UUID) -> None:
        """Revoke an API key. Silent no-op if not owned by this tenant."""
        await self.api_key_repo.revoke(tenant_id, key_id)
        logger.info(
            "api_key.revoked",
            tenant_id=str(tenant_id),
            api_key_id=str(key_id),
        )
