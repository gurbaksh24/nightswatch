"""IntegrationRepository — tenant-scoped CRUD over the ``integration`` table.

Inherits the standard scoping helpers from :class:`TenantScopedRepository`
so every query carries ``WHERE tenant_id = :tenant_id`` automatically.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sre.core._base.repository import TenantScopedRepository
from ai_sre.exceptions import IntegrationAlreadyExists
from ai_sre.models.integration import Integration


class IntegrationRepository(TenantScopedRepository[Integration]):
    """CRUD for :class:`Integration`, scoped to a single tenant."""

    model = Integration

    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        super().__init__(session, tenant_id)

    async def get_by_id(self, integration_id: UUID) -> Integration | None:
        """Return the integration owned by this tenant, or ``None``."""
        return await self.get(integration_id)

    async def list_all(self) -> Sequence[Integration]:
        """List integrations for this tenant, newest first.

        Tiebreak on ``id DESC`` (UUIDv7, time-ordered) so the order is
        deterministic when two rows share a transaction-time ``created_at``.
        """
        stmt = (
            self._scoped(select(Integration))
            .order_by(Integration.created_at.desc(), Integration.id.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def create(
        self,
        *,
        kind: str,
        name: str,
        config_encrypted: bytes,
        config_public: dict[str, Any],
    ) -> Integration:
        """Insert a new integration. Raises :class:`IntegrationAlreadyExists`
        on a duplicate ``(tenant_id, kind, name)``."""
        row = Integration(
            tenant_id=self.tenant_id,
            kind=kind,
            name=name,
            config_encrypted=config_encrypted,
            config_public=config_public,
            status="pending",
        )
        self.session.add(row)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise IntegrationAlreadyExists(
                f"Integration ({kind}, {name}) already exists for this tenant.",
                details={"kind": kind, "name": name},
            ) from exc
        await self.session.refresh(row)
        return row

    async def delete(self, integration_id: UUID) -> bool:
        """Hard-delete an integration. Returns ``True`` if a row was removed."""
        row = await self.get(integration_id)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True

    async def set_webhook_secret(
        self, integration_id: UUID, encrypted: bytes
    ) -> Integration | None:
        """Store the envelope-encrypted webhook signing secret. Returns the
        updated row, or ``None`` if not owned by this tenant."""
        row = await self.get(integration_id)
        if row is None:
            return None
        row.webhook_signing_secret_encrypted = encrypted
        await self.session.flush()
        return row
