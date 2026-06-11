"""Unit test: dry_run suppresses delivery in the orchestrator (spec 0016)."""

from __future__ import annotations

from typing import Any

import pytest

from ai_sre.core.investigation.budget import Budget
from ai_sre.core.investigation.context import InvestigationContext, Report
from ai_sre.core.investigation.orchestrator import InvestigationOrchestrator
from ai_sre.core.tenant.context import TenantContext
from ai_sre.utils.ids import new_id


class _SpyDispatcher:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def dispatch(self, report: Any, configs: Any) -> list[Any]:
        self.calls.append((report, configs))
        return []


def _orch(dispatcher: _SpyDispatcher) -> InvestigationOrchestrator:
    # Only the delivery collaborators matter for _dispatch_delivery.
    return InvestigationOrchestrator(
        pipeline=None,  # type: ignore[arg-type]
        repo=None,  # type: ignore[arg-type]
        delivery_dispatcher=dispatcher,  # type: ignore[arg-type]
        delivery_configs={"slack": {"channel_id": "C1", "bot_token": "x"}},
    )


def _ctx(*, dry_run: bool) -> InvestigationContext:
    ctx = InvestigationContext(
        tenant=TenantContext(tenant_id=new_id(), name="t", slug="t", api_key_id=new_id()),
        investigation_id=new_id(),
        alert={}, service={}, dependencies={}, metric_catalog={}, budget=Budget(),
    )
    ctx.report = Report(headline="boom", confidence="high")
    ctx.dry_run = dry_run
    return ctx


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dry_run_skips_delivery() -> None:
    spy = _SpyDispatcher()
    await _orch(spy)._dispatch_delivery(_ctx(dry_run=True))
    assert spy.calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_dry_run_delivers() -> None:
    spy = _SpyDispatcher()
    await _orch(spy)._dispatch_delivery(_ctx(dry_run=False))
    assert len(spy.calls) == 1
