"""Unit tests for search_runbooks + search_past_incidents (spec 0015)."""

from __future__ import annotations

from typing import Any

import pytest

from ai_sre.core.investigation.budget import Budget
from ai_sre.core.investigation.context import InvestigationContext
from ai_sre.core.knowledge.service import KnowledgeSearchResult
from ai_sre.core.tenant.context import TenantContext
from ai_sre.llm.tools.search_past_incidents import search_past_incidents_handler
from ai_sre.llm.tools.search_runbooks import search_runbooks_handler
from ai_sre.utils.ids import new_id


class _FakeKnowledge:
    """Stands in for KnowledgeService; records calls, returns canned hits."""

    def __init__(self, hits: list[KnowledgeSearchResult] | None = None) -> None:
        self.hits = hits or []
        self.calls: list[dict[str, Any]] = []

    async def search(
        self, query: str, k: int = 5, *, kinds: list[str] | None = None
    ) -> list[KnowledgeSearchResult]:
        self.calls.append({"query": query, "k": k, "kinds": kinds})
        return self.hits


def _hit(text: str, score: float, *, title: str = "Doc") -> KnowledgeSearchResult:
    return KnowledgeSearchResult(
        doc_id=new_id(), chunk_id=new_id(), title=title,
        text=text, headings=["Runbook", "Database"], score=score,
    )


def _ctx(knowledge: Any = None) -> InvestigationContext:
    ctx = InvestigationContext(
        tenant=TenantContext(tenant_id=new_id(), name="t", slug="t", api_key_id=new_id()),
        investigation_id=new_id(),
        alert={"alert_name": "HighErrorRate"},
        service={"name": "checkout"},
        dependencies={},
        metric_catalog={},
        budget=Budget(),
    )
    ctx.knowledge = knowledge
    return ctx


@pytest.mark.unit
@pytest.mark.asyncio
async def test_search_runbooks_returns_ranked_results() -> None:
    fake = _FakeKnowledge([_hit("restart pgbouncer", 0.9), _hit("warm the cache", 0.4)])
    out = await search_runbooks_handler({"query": "pool exhausted"}, _ctx(fake))
    assert [r["text"] for r in out["results"]] == ["restart pgbouncer", "warm the cache"]
    assert out["results"][0]["section"] == "Runbook > Database"
    assert out["results"][0]["score"] == 0.9
    assert fake.calls == [{"query": "pool exhausted", "k": 5, "kinds": ["runbook"]}]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_search_past_incidents_filters_by_kind() -> None:
    fake = _FakeKnowledge([_hit("previous outage", 0.8, title="Likely cause: pool")])
    out = await search_past_incidents_handler({"query": "checkout errors", "k": 3}, _ctx(fake))
    assert out["results"][0]["title"] == "Likely cause: pool"
    assert fake.calls == [{"query": "checkout errors", "k": 3, "kinds": ["past_investigation"]}]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler", [search_runbooks_handler, search_past_incidents_handler]
)
async def test_no_knowledge_wired_degrades_to_empty(handler: Any) -> None:
    out = await handler({"query": "anything"}, _ctx(knowledge=None))
    assert out["results"] == []
    assert "note" in out


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler", [search_runbooks_handler, search_past_incidents_handler]
)
async def test_empty_query_degrades_to_empty(handler: Any) -> None:
    fake = _FakeKnowledge([_hit("x", 0.5)])
    out = await handler({"query": "   "}, _ctx(fake))
    assert out["results"] == []
    assert fake.calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_k_is_clamped_to_max() -> None:
    fake = _FakeKnowledge()
    await search_runbooks_handler({"query": "q", "k": 50}, _ctx(fake))
    assert fake.calls[0]["k"] == 10


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_matching_docs_returns_empty_list() -> None:
    out = await search_past_incidents_handler({"query": "q"}, _ctx(_FakeKnowledge()))
    assert out == {"results": []}
