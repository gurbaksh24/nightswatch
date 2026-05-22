"""Unit tests for `TenantService`.

These tests use an in-memory fake `TenantRepository` so the service logic can
be exercised without a real DB. The real repository is covered by the
integration test in `tests/integration/test_auth_flow.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import UUID

import pytest

from ai_sre.core.tenant.service import TenantService
from ai_sre.exceptions import TenantNotFound, TenantSlugTaken
from ai_sre.models.tenant import Tenant
from ai_sre.utils.ids import new_id


class _FakeTenantRepository:
    """In-memory stand-in for `TenantRepository`."""

    def __init__(self) -> None:
        self._by_id: dict[UUID, Tenant] = {}
        self._by_slug: dict[str, Tenant] = {}

    async def get(self, tenant_id: UUID) -> Tenant | None:
        return self._by_id.get(tenant_id)

    async def get_by_slug(self, slug: str) -> Tenant | None:
        return self._by_slug.get(slug)

    async def create(self, *, name: str, slug: str) -> Tenant:
        if slug in self._by_slug:
            raise TenantSlugTaken(f"Slug '{slug}' is already taken.")
        now = datetime.now(timezone.utc)
        tenant = Tenant(id=new_id(), name=name, slug=slug, status="active")
        # Server defaults don't fire without a flush; emulate them for assertions.
        tenant.created_at = now
        tenant.updated_at = now
        self._by_id[tenant.id] = tenant
        self._by_slug[slug] = tenant
        return tenant

    async def update(self, tenant_id: UUID, *, name: str | None = None) -> Tenant | None:
        tenant = self._by_id.get(tenant_id)
        if tenant is None:
            return None
        if name is not None:
            tenant.name = name
        return tenant


@pytest.fixture
def service() -> TenantService:
    return TenantService(_FakeTenantRepository())  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_persists_and_returns(service: TenantService) -> None:
    tenant = await service.create(name="Acme", slug="acme")
    assert tenant.name == "Acme"
    assert tenant.slug == "acme"
    assert tenant.status == "active"
    fetched = await service.get(tenant.id)
    assert fetched is not None
    assert fetched.id == tenant.id


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_duplicate_slug_raises(service: TenantService) -> None:
    await service.create(name="Acme", slug="acme")
    with pytest.raises(TenantSlugTaken):
        await service.create(name="Acme 2", slug="acme")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_by_slug(service: TenantService) -> None:
    created = await service.create(name="Acme", slug="acme")
    fetched = await service.get_by_slug("acme")
    assert fetched is not None
    assert fetched.id == created.id
    assert await service.get_by_slug("nope") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_name(service: TenantService) -> None:
    created = await service.create(name="Old", slug="acme")
    updated = await service.update(created.id, name="New")
    assert updated.name == "New"
    assert updated.id == created.id


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_missing_raises(service: TenantService) -> None:
    with pytest.raises(TenantNotFound):
        await service.update(new_id(), name="x")
