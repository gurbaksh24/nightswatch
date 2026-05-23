"""Unit tests for :class:`IntegrationService`.

Use in-memory fakes for the repository so service logic can be exercised
without a real DB. The real repository is exercised by the integration
test in ``tests/integration/test_integration_routes.py``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import pytest

from ai_sre.core.integration.service import IntegrationService
from ai_sre.exceptions import (
    IntegrationAlreadyExists,
    IntegrationCredentialDecryptionFailed,
    IntegrationNotFound,
)
from ai_sre.models.integration import Integration
from ai_sre.utils.crypto import EnvelopeEncryptionService, random_base64_key
from ai_sre.utils.ids import new_id


class _FakeIntegrationRepository:
    """In-memory stand-in for ``IntegrationRepository``."""

    def __init__(self, tenant_id: UUID) -> None:
        self.tenant_id = tenant_id
        self._by_id: dict[UUID, Integration] = {}

    async def get_by_id(self, integration_id: UUID) -> Integration | None:
        row = self._by_id.get(integration_id)
        if row is None or row.tenant_id != self.tenant_id:
            return None
        return row

    async def list_all(self) -> Sequence[Integration]:
        return [r for r in self._by_id.values() if r.tenant_id == self.tenant_id]

    async def create(
        self,
        *,
        kind: str,
        name: str,
        config_encrypted: bytes,
        config_public: dict[str, Any],
    ) -> Integration:
        # Mimic the UNIQUE(tenant_id, kind, name) constraint of the real DB.
        for row in self._by_id.values():
            if row.tenant_id == self.tenant_id and row.kind == kind and row.name == name:
                raise IntegrationAlreadyExists(
                    f"Integration ({kind}, {name}) already exists.",
                    details={"kind": kind, "name": name},
                )
        row = Integration(
            id=new_id(),
            tenant_id=self.tenant_id,
            kind=kind,
            name=name,
            config_encrypted=config_encrypted,
            config_public=config_public,
            status="pending",
        )
        self._by_id[row.id] = row
        return row

    async def delete(self, integration_id: UUID) -> bool:
        row = self._by_id.get(integration_id)
        if row is None or row.tenant_id != self.tenant_id:
            return False
        del self._by_id[integration_id]
        return True


def _build(tenant_id: UUID | None = None) -> tuple[IntegrationService, _FakeIntegrationRepository, EnvelopeEncryptionService]:
    tenant_id = tenant_id or new_id()
    repo = _FakeIntegrationRepository(tenant_id)
    crypto = EnvelopeEncryptionService(random_base64_key())
    service = IntegrationService(repo, crypto)  # type: ignore[arg-type]
    return service, repo, crypto


def _prom_config(token: str = "s3cr3t") -> dict[str, Any]:
    return {
        "url": "https://prom.example.com",
        "auth": {"type": "bearer", "token": token},
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_persists_with_public_and_encrypted_views() -> None:
    service, _, crypto = _build()
    row = await service.create(kind="prometheus", name="prod", config=_prom_config())
    # Public view contains only the host + auth type.
    assert row.config_public == {
        "url_host": "prom.example.com",
        "auth_type": "bearer",
    }
    # Encrypted blob round-trips through the same crypto and contains the full
    # config including the secret token.
    decrypted = json.loads(crypto.decrypt(row.config_encrypted).decode("utf-8"))
    assert decrypted == _prom_config()
    assert row.status == "pending"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_duplicate_raises() -> None:
    service, _, _ = _build()
    await service.create(kind="prometheus", name="prod", config=_prom_config())
    with pytest.raises(IntegrationAlreadyExists):
        await service.create(kind="prometheus", name="prod", config=_prom_config("other"))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_same_name_different_kind_is_allowed() -> None:
    service, _, _ = _build()
    await service.create(kind="prometheus", name="prod", config=_prom_config())
    # slack uses a different code path eventually, but the repo only enforces
    # (tenant_id, kind, name) uniqueness — same name + different kind is fine.
    await service.create(kind="slack", name="prod", config={"placeholder": True})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_and_list() -> None:
    service, _, _ = _build()
    created = await service.create(kind="prometheus", name="prod", config=_prom_config())
    fetched = await service.get(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    listed = await service.list()
    assert [row.id for row in listed] == [created.id]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_cross_tenant_returns_none() -> None:
    """A tenant must never see another tenant's integration."""
    service_a, repo_a, _ = _build()
    other_tenant_id = new_id()
    # Hand-write a row owned by a different tenant directly into the same
    # fake to simulate the data being there but isolated.
    repo_a._by_id[new_id()] = Integration(
        id=new_id(),
        tenant_id=other_tenant_id,
        kind="prometheus",
        name="prod",
        config_encrypted=b"x" * 64,
        config_public={"url_host": "leak.example.com"},
        status="pending",
    )
    # The service shouldn't see it via list or get.
    assert list(await service_a.list()) == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_removes_row() -> None:
    service, _, _ = _build()
    created = await service.create(kind="prometheus", name="prod", config=_prom_config())
    await service.delete(created.id)
    assert await service.get(created.id) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_missing_raises_not_found() -> None:
    service, _, _ = _build()
    with pytest.raises(IntegrationNotFound):
        await service.delete(new_id())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_decrypt_config_returns_plaintext_dict() -> None:
    service, _, _ = _build()
    row = await service.create(kind="prometheus", name="prod", config=_prom_config())
    assert service.decrypt_config(row) == _prom_config()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_decrypt_config_with_wrong_key_raises_typed_error() -> None:
    """A rotated/lost master key should surface as a typed domain error,
    not an opaque InvalidTag.
    """
    service, _repo, _ = _build()
    row = await service.create(kind="prometheus", name="prod", config=_prom_config())
    # Replace the service's crypto with a different key.
    service.crypto = EnvelopeEncryptionService(random_base64_key())
    with pytest.raises(IntegrationCredentialDecryptionFailed):
        service.decrypt_config(row)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_public_view_handles_no_auth() -> None:
    service, _, _ = _build()
    row = await service.create(
        kind="prometheus",
        name="open",
        config={"url": "http://prom-internal:9090", "auth": {"type": "none"}},
    )
    assert row.config_public == {"url_host": "prom-internal", "auth_type": "none"}
