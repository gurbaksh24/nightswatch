"""Integration tests for runbook auto-ingestion (spec 0021).

Real DB + hashing embedder; the HTTP fetch is monkeypatched at the module
seam (network is off-limits in tests).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_sre.core.knowledge import runbook_fetcher
from ai_sre.core.knowledge import service as knowledge_service_module
from ai_sre.core.knowledge.embedding import HashingEmbedder
from ai_sre.core.knowledge.repository import KnowledgeRepository
from ai_sre.core.knowledge.service import KnowledgeService
from ai_sre.core.tenant.repository import TenantRepository
from ai_sre.core.tenant.service import TenantService
from ai_sre.db import session_scope

pytestmark = pytest.mark.integration

_URL = "https://runbooks.example/checkout.md"
_BODY = "# Checkout runbook\n\n## Remediation\n\nRestart the checkout pods."


@pytest.fixture(autouse=True)
def _fake_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fetch(url: str, **_kwargs: Any) -> runbook_fetcher.FetchedRunbook:
        return runbook_fetcher.FetchedRunbook(
            text=_BODY, is_markdown=True, content_type="text/markdown"
        )

    # Patch the seam the service imports through.
    monkeypatch.setattr(runbook_fetcher, "fetch_runbook", _fetch)


async def test_ingest_dedupe_and_search(db_engine: AsyncEngine) -> None:
    async with session_scope() as session:
        tenant = await TenantService(TenantRepository(session)).create(name="Acme", slug="acme")
        svc = KnowledgeService(KnowledgeRepository(session, tenant.id), HashingEmbedder())

        outcome = await svc.ingest_runbook_from_url(_URL, timeout_seconds=5, max_bytes=1024)
        assert outcome == "ingested"

        # Second alert with the same runbook_url: no duplicate.
        outcome = await svc.ingest_runbook_from_url(_URL, timeout_seconds=5, max_bytes=1024)
        assert outcome == "already_ingested"

        docs = await svc.repo.list_docs()
        assert len(docs) == 1
        doc = docs[0]
        assert doc.kind == "runbook"
        assert doc.source_object_key == _URL
        assert doc.extra_metadata["auto_ingested"] is True
        assert doc.title == "checkout.md"

        # The runbook is searchable the way the search_runbooks tool asks.
        hits = await svc.search("restart checkout pods", k=3, kinds=["runbook"])
        assert hits and hits[0].doc_id == doc.id
        assert "Restart the checkout pods." in hits[0].text


# Sanity: the seam actually exists — if fetch_runbook is renamed/moved, the
# autouse monkeypatch above would silently patch nothing.
def test_patch_seam_exists() -> None:
    assert hasattr(runbook_fetcher, "fetch_runbook")
    assert knowledge_service_module is not None
