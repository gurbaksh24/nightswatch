"""Integration tests for spec 0015: past-incident ingest + knowledge tools.

Drives the real worker entrypoint (`run_investigation`, keyless → fallback
report) against the integration Postgres, then verifies:

    * a ``past_investigation`` knowledge doc (with embedded chunks) exists
      after a successful run;
    * a second, similar investigation can find it via the
      ``search_past_incidents`` tool (the spec's DoD);
    * an ingest failure never breaks the investigation itself.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_sre.config import get_settings
from ai_sre.core.alert.repository import AlertRepository
from ai_sre.core.investigation.budget import Budget
from ai_sre.core.investigation.context import InvestigationContext
from ai_sre.core.investigation.repository import InvestigationRepository
from ai_sre.core.knowledge.embedding import HashingEmbedder
from ai_sre.core.knowledge.repository import KnowledgeRepository
from ai_sre.core.knowledge.service import KnowledgeService
from ai_sre.core.service.repository import ServiceRepository
from ai_sre.core.tenant.context import TenantContext
from ai_sre.core.tenant.repository import TenantRepository
from ai_sre.core.tenant.service import TenantService
from ai_sre.db import session_scope
from ai_sre.llm.tools.search_past_incidents import search_past_incidents_handler
from ai_sre.models.investigation import Investigation
from ai_sre.models.knowledge import KnowledgeChunk, KnowledgeDoc
from ai_sre.utils.ids import new_id
from ai_sre.workers.app import run_investigation


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SRE_EMBEDDING_PROVIDER", "hashing")
    get_settings.cache_clear()


async def _seed(*, slug: str, fingerprint: str = "fp-1") -> tuple[UUID, UUID]:
    """Create tenant + service + alert + pending investigation."""
    now = datetime.now(UTC)
    async with session_scope() as session:
        tenant = await TenantService(TenantRepository(session)).create(name=slug, slug=slug)
        service = await ServiceRepository(session, tenant.id).create(
            name="checkout", label_selector={"app": "checkout"}, ownership=None, slo_config=None
        )
        alert = await AlertRepository(session, tenant.id).create(
            source="alertmanager", received_at=now, raw_payload={},
            alert_name="HighErrorRate", severity="critical", status="firing",
            started_at=now, ended_at=None,
            labels={"alertname": "HighErrorRate"}, annotations={}, fingerprint=fingerprint,
        )
        inv = await InvestigationRepository(session, tenant.id).create_pending(
            service_id=service.id, triggering_alert_id=alert.id, fingerprint=fingerprint
        )
    return tenant.id, inv.id


def _tool_ctx(tenant_id: UUID, knowledge: KnowledgeService) -> InvestigationContext:
    ctx = InvestigationContext(
        tenant=TenantContext(tenant_id=tenant_id, name="t", slug="t", api_key_id=new_id()),
        investigation_id=new_id(),
        alert={"alert_name": "HighErrorRate"},
        service={"name": "checkout"},
        dependencies={},
        metric_catalog={},
        budget=Budget(),
    )
    ctx.knowledge = knowledge
    return ctx


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_ingests_past_investigation(db_engine: AsyncEngine) -> None:
    tenant_id, inv_id = await _seed(slug="acme")
    await run_investigation(str(inv_id))

    async with session_scope() as session:
        docs = (
            (await session.execute(
                select(KnowledgeDoc).where(
                    KnowledgeDoc.tenant_id == tenant_id,
                    KnowledgeDoc.kind == "past_investigation",
                )
            )).scalars().all()
        )
        assert len(docs) == 1
        assert docs[0].extra_metadata.get("investigation_id") == str(inv_id)
        chunks = (
            (await session.execute(
                select(KnowledgeChunk).where(KnowledgeChunk.doc_id == docs[0].id)
            )).scalars().all()
        )
        assert chunks, "RCA body must have been chunked + embedded"
        assert all(c.embedding is not None for c in chunks)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_second_alert_finds_first_investigation(db_engine: AsyncEngine) -> None:
    """The spec's DoD: a second similar alert's search returns the first
    investigation as the top result."""
    tenant_id, first_inv = await _seed(slug="acme")
    await run_investigation(str(first_inv))

    # Second similar investigation: its hypothesis stage would call
    # search_past_incidents — drive the real tool handler.
    async with session_scope() as session:
        knowledge = KnowledgeService(
            KnowledgeRepository(session, tenant_id), HashingEmbedder()
        )
        out = await search_past_incidents_handler(
            {"query": "HighErrorRate on checkout"},
            _tool_ctx(tenant_id, knowledge),
        )

    assert out["results"], "expected the first investigation to be found"
    top = out["results"][0]
    assert "HighErrorRate" in top["text"]
    assert "checkout" in top["text"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_past_incidents_are_tenant_isolated(db_engine: AsyncEngine) -> None:
    _, first_inv = await _seed(slug="a")
    await run_investigation(str(first_inv))
    other_tenant, _ = await _seed(slug="b", fingerprint="fp-2")

    async with session_scope() as session:
        knowledge = KnowledgeService(
            KnowledgeRepository(session, other_tenant), HashingEmbedder()
        )
        out = await search_past_incidents_handler(
            {"query": "HighErrorRate on checkout"},
            _tool_ctx(other_tenant, knowledge),
        )
    assert out["results"] == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_failure_does_not_fail_investigation(
    db_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Embedding blowing up mid-ingest must not roll back the investigation."""
    _, inv_id = await _seed(slug="acme")

    async def _boom(self: KnowledgeService, **kwargs: object) -> None:
        raise RuntimeError("embedding backend down")

    monkeypatch.setattr(KnowledgeService, "ingest_past_investigation", _boom)
    await run_investigation(str(inv_id))  # must not raise

    async with session_scope() as session:
        inv = await session.get(Investigation, inv_id)
        assert inv is not None and inv.status == "succeeded"
        docs = (
            (await session.execute(
                select(KnowledgeDoc).where(KnowledgeDoc.kind == "past_investigation")
            )).scalars().all()
        )
        assert docs == []
