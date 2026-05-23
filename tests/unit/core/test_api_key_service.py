"""Unit tests for `ApiKeyService`.

Uses in-memory fakes for `ApiKeyRepository` and `TenantRepository`. Asserts
that:
    * `issue` returns the plaintext key once and stores its hash.
    * `verify` accepts active keys, rejects revoked / expired / unknown ones,
      and rejects keys belonging to a non-active tenant.
    * `list` returns rows newest-first.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from ai_sre.core.tenant.api_key_service import ApiKeyService
from ai_sre.models.api_key import ApiKey
from ai_sre.models.tenant import Tenant
from ai_sre.utils.crypto import hash_api_key
from ai_sre.utils.ids import new_id

# Baseline for the fake's monotonically-increasing created_at values. Using
# a fixed epoch + a per-row offset avoids flakes when two issues land in the
# same microsecond.
_FAKE_EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)


class _FakeTenantRepository:
    def __init__(self) -> None:
        self._by_id: dict[UUID, Tenant] = {}

    async def get(self, tenant_id: UUID) -> Tenant | None:
        return self._by_id.get(tenant_id)

    def add(self, tenant: Tenant) -> None:
        self._by_id[tenant.id] = tenant


class _FakeApiKeyRepository:
    def __init__(self) -> None:
        self._by_id: dict[UUID, ApiKey] = {}
        self._by_hash: dict[str, ApiKey] = {}
        self._tick = 0

    async def find_by_hash(self, key_hash: str) -> ApiKey | None:
        return self._by_hash.get(key_hash)

    async def list_for_tenant(self, tenant_id: UUID) -> list[ApiKey]:
        # Mirror the real repository's order: created_at DESC, id DESC.
        rows = [row for row in self._by_id.values() if row.tenant_id == tenant_id]
        rows.sort(key=lambda r: (r.created_at, r.id.int), reverse=True)
        return rows

    async def create(
        self, *, tenant_id: UUID, name: str, key_hash: str, prefix: str
    ) -> ApiKey:
        row = ApiKey(
            id=new_id(),
            tenant_id=tenant_id,
            name=name,
            key_hash=key_hash,
            prefix=prefix,
        )
        # Strictly monotonic timestamp so list-order tests are deterministic
        # regardless of clock resolution.
        self._tick += 1
        now = _FAKE_EPOCH + timedelta(seconds=self._tick)
        row.created_at = now
        row.updated_at = now
        self._by_id[row.id] = row
        self._by_hash[key_hash] = row
        return row

    async def revoke(self, tenant_id: UUID, key_id: UUID) -> None:
        row = self._by_id.get(key_id)
        if row is None or row.tenant_id != tenant_id:
            return
        if row.revoked_at is None:
            row.revoked_at = datetime.now(timezone.utc)

    async def mark_used(self, api_key_id: UUID) -> None:
        row = self._by_id.get(api_key_id)
        if row is not None:
            row.last_used_at = datetime.now(timezone.utc)


def _build() -> tuple[ApiKeyService, _FakeTenantRepository, _FakeApiKeyRepository, Tenant]:
    trepo = _FakeTenantRepository()
    krepo = _FakeApiKeyRepository()
    service = ApiKeyService(krepo, trepo)  # type: ignore[arg-type]
    tenant = Tenant(id=new_id(), name="Acme", slug="acme", status="active")
    now = datetime.now(timezone.utc)
    tenant.created_at = now
    tenant.updated_at = now
    trepo.add(tenant)
    return service, trepo, krepo, tenant


@pytest.mark.unit
@pytest.mark.asyncio
async def test_issue_returns_plaintext_and_stores_hash() -> None:
    service, _, krepo, tenant = _build()
    issued = await service.issue(tenant.id, name="ci-key")
    assert issued.key.startswith("ai-sre-live-")
    assert issued.prefix == issued.key[:8]
    stored = krepo._by_id[issued.id]
    # Plaintext is never persisted.
    assert stored.key_hash == hash_api_key(issued.key)
    assert stored.key_hash != issued.key


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_happy_path() -> None:
    service, _, _, tenant = _build()
    issued = await service.issue(tenant.id, name="ci-key")
    ctx = await service.verify(issued.key)
    assert ctx is not None
    assert ctx.tenant_id == tenant.id
    assert ctx.api_key_id == issued.id
    assert ctx.slug == tenant.slug


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_revoked_returns_none() -> None:
    service, _, _, tenant = _build()
    issued = await service.issue(tenant.id, name="ci-key")
    await service.revoke(tenant.id, issued.id)
    assert await service.verify(issued.key) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_expired_returns_none() -> None:
    service, _, krepo, tenant = _build()
    issued = await service.issue(tenant.id, name="ci-key")
    krepo._by_id[issued.id].expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    assert await service.verify(issued.key) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_unknown_returns_none() -> None:
    service, _, _, _ = _build()
    assert await service.verify("ai-sre-live-bogus") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_suspended_tenant_returns_none() -> None:
    service, trepo, _, tenant = _build()
    issued = await service.issue(tenant.id, name="ci-key")
    trepo._by_id[tenant.id].status = "suspended"
    assert await service.verify(issued.key) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_returns_rows_newest_first() -> None:
    service, _, _, tenant = _build()
    await service.issue(tenant.id, name="a")
    await service.issue(tenant.id, name="b")
    rows = await service.list(tenant.id)
    assert [row.name for row in rows] == ["b", "a"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_revoke_other_tenants_key_is_noop() -> None:
    service, trepo, krepo, tenant = _build()
    issued = await service.issue(tenant.id, name="a")
    other = Tenant(id=new_id(), name="Other", slug="other", status="active")
    now = datetime.now(timezone.utc)
    other.created_at = now
    other.updated_at = now
    trepo.add(other)
    # Attempt cross-tenant revoke should silently no-op.
    await service.revoke(other.id, issued.id)
    assert krepo._by_id[issued.id].revoked_at is None
