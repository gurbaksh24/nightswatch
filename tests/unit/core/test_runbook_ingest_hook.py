"""Unit tests for the orchestrator's runbook-ingest hook (spec 0021)."""

from __future__ import annotations

from typing import Any

import pytest

from ai_sre.config import get_settings
from ai_sre.core.investigation.budget import Budget
from ai_sre.core.investigation.context import InvestigationContext
from ai_sre.core.investigation.orchestrator import InvestigationOrchestrator
from ai_sre.core.tenant.context import TenantContext
from ai_sre.utils.ids import new_id

pytestmark = pytest.mark.unit


class _FakeKnowledge:
    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[str] = []
        self._raises = raises

    async def ingest_runbook_from_url(self, url: str, **_kwargs: Any) -> str:
        self.calls.append(url)
        if self._raises:
            raise RuntimeError("fetch blew up")
        return "ingested"


def _orchestrator(knowledge: _FakeKnowledge | None) -> InvestigationOrchestrator:
    # The hook only touches self.knowledge and settings; pipeline/repo are
    # unused, so light sentinels keep the test honest without a DB.
    return InvestigationOrchestrator(
        pipeline=None,  # type: ignore[arg-type]
        repo=None,  # type: ignore[arg-type]
        knowledge=knowledge,  # type: ignore[arg-type]
    )


def _ctx(annotations: dict[str, str]) -> InvestigationContext:
    return InvestigationContext(
        tenant=TenantContext(tenant_id=new_id(), name="t", slug="t", api_key_id=new_id()),
        investigation_id=new_id(),
        alert={"alert_name": "x", "annotations": annotations},
        service={},
        dependencies={},
        metric_catalog={},
        budget=Budget(),
    )


@pytest.mark.asyncio
async def test_ingests_when_annotation_present() -> None:
    knowledge = _FakeKnowledge()
    await _orchestrator(knowledge)._ingest_alert_runbook(
        _ctx({"runbook_url": "https://runbooks.example/checkout"})
    )
    assert knowledge.calls == ["https://runbooks.example/checkout"]


@pytest.mark.asyncio
async def test_skips_without_annotation_or_knowledge() -> None:
    knowledge = _FakeKnowledge()
    await _orchestrator(knowledge)._ingest_alert_runbook(_ctx({}))
    assert knowledge.calls == []
    # No knowledge service wired → no crash.
    await _orchestrator(None)._ingest_alert_runbook(
        _ctx({"runbook_url": "https://runbooks.example/x"})
    )


@pytest.mark.asyncio
async def test_failure_never_raises() -> None:
    knowledge = _FakeKnowledge(raises=True)
    await _orchestrator(knowledge)._ingest_alert_runbook(
        _ctx({"runbook_url": "https://runbooks.example/x"})
    )
    assert knowledge.calls  # attempted, swallowed


@pytest.mark.asyncio
async def test_flag_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SRE_RUNBOOK_FETCH_ENABLED", "false")
    get_settings.cache_clear()
    try:
        knowledge = _FakeKnowledge()
        await _orchestrator(knowledge)._ingest_alert_runbook(
            _ctx({"runbook_url": "https://runbooks.example/x"})
        )
        assert knowledge.calls == []
    finally:
        get_settings.cache_clear()
