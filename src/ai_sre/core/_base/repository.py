"""Tenant-scoped repository base.

Every repository in `core/` MUST inherit from `TenantScopedRepository`. This
is the seam that enforces NFR-5.5 (tenant isolation at the data layer): no
query goes out without a `WHERE tenant_id = :tenant_id` clause.

Subclasses set `model` and `id_field` as class vars; common CRUD operations
are inherited.
"""

from __future__ import annotations

from typing import Any, ClassVar, Generic, Sequence, TypeVar
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sre.db import Base

ModelT = TypeVar("ModelT", bound=Base)


class TenantScopedRepository(Generic[ModelT]):
    """Base for tenant-scoped repositories.

    Subclass and set `model`. All queries built via `_scoped()` automatically
    include the tenant filter.
    """

    model: ClassVar[type[Base]]

    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    def _scoped(self, stmt: Select[Any]) -> Select[Any]:
        """Inject `tenant_id = :tenant_id` into the given Select."""
        return stmt.where(self.model.tenant_id == self.tenant_id)  # type: ignore[attr-defined]

    async def get(self, id_: UUID) -> ModelT | None:
        """Fetch a single row by primary key, scoped to this tenant."""
        stmt = self._scoped(select(self.model).where(self.model.id == id_))  # type: ignore[attr-defined]
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()  # type: ignore[return-value]

    async def list(self, *, limit: int = 100, offset: int = 0) -> Sequence[ModelT]:
        stmt = self._scoped(select(self.model)).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return result.scalars().all()  # type: ignore[return-value]

    async def add(self, obj: ModelT) -> ModelT:
        # Defensive: ensure the model carries our tenant_id.
        if getattr(obj, "tenant_id", None) != self.tenant_id:
            raise ValueError(
                "Refusing to add: tenant_id on object does not match repository tenant."
            )
        self.session.add(obj)
        await self.session.flush()
        return obj
