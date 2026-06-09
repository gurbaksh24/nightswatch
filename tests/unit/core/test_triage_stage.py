"""Unit tests for TriageStage classification (spec 0007).

Pure logic test with in-memory fakes for the two repos — no DB.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from uuid import UUID

import pytest

from ai_sre.core.investigation.budget import Budget
from ai_sre.core.investigation.context import InvestigationContext
from ai_sre.core.investigation.stages.triage import TriageStage
from ai_sre.core.tenant.context import TenantContext
from ai_sre.models.investigation import Investigation
from ai_sre.utils.ids import new_id

TENANT = new_id()
INV_ID = new_id()


class _FakeInvRepo:
    def __init__(self, priors: list[Investigation] | None = None) -> None:
        self._priors = priors or []

    async def find_by_fingerprint(
        self,
        fingerprint: str,
        *,
        since: datetime,
        exclude_id: UUID | None = None,
        statuses: Iterable[str] | None = None,
    ) -> Sequence[Investigation]:
        return self._priors


class _FakeAlertRepo:
    def __init__(self, count: int = 0) -> None:
        self._count = count

    async def count_by_fingerprint(self, fingerprint: str, *, since: datetime) -> int:
        return self._count


def _ctx(*, fingerprint: str | None = "fp-abc") -> InvestigationContext:
    alert = {"alert_name": "HighErrorRate"}
    if fingerprint is not None:
        alert["fingerprint"] = fingerprint
    return InvestigationContext(
        tenant=TenantContext(tenant_id=TENANT, name="t", slug="t", api_key_id=new_id()),
        investigation_id=INV_ID,
        alert=alert,
        service={},
        dependencies={},
        metric_catalog={},
        budget=Budget(),
    )


def _prior(status: str = "succeeded") -> Investigation:
    return Investigation(
        id=new_id(), tenant_id=TENANT, service_id=new_id(),
        fingerprint="fp-abc", status=status,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_novel_when_no_priors_and_no_flapping() -> None:
    stage = TriageStage(_FakeInvRepo(), _FakeAlertRepo(count=1))  # type: ignore[arg-type]
    ctx = _ctx()
    result = await stage.execute(ctx)
    assert result.status == "succeeded"
    assert ctx.triage is not None and ctx.triage.classification == "novel"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_noise_when_flapping() -> None:
    stage = TriageStage(_FakeInvRepo(), _FakeAlertRepo(count=9), noise_threshold=5)  # type: ignore[arg-type]
    ctx = _ctx()
    await stage.execute(ctx)
    assert ctx.triage is not None and ctx.triage.classification == "noise"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_known_issue_when_prior_succeeded() -> None:
    prior = _prior("succeeded")
    stage = TriageStage(_FakeInvRepo([prior]), _FakeAlertRepo(count=0))  # type: ignore[arg-type]
    ctx = _ctx()
    await stage.execute(ctx)
    assert ctx.triage is not None
    assert ctx.triage.classification == "known_issue"
    assert ctx.triage.related_investigation_id == prior.id


@pytest.mark.unit
@pytest.mark.asyncio
async def test_noise_takes_precedence_over_known_issue() -> None:
    stage = TriageStage(_FakeInvRepo([_prior()]), _FakeAlertRepo(count=99), noise_threshold=5)  # type: ignore[arg-type]
    ctx = _ctx()
    await stage.execute(ctx)
    assert ctx.triage is not None and ctx.triage.classification == "noise"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_fingerprint_is_novel() -> None:
    stage = TriageStage(_FakeInvRepo([_prior()]), _FakeAlertRepo(count=99))  # type: ignore[arg-type]
    ctx = _ctx(fingerprint=None)
    await stage.execute(ctx)
    assert ctx.triage is not None and ctx.triage.classification == "novel"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_repos_degrades_to_novel() -> None:
    stage = TriageStage()  # Pipeline.default() style — no DB access
    ctx = _ctx()
    await stage.execute(ctx)
    assert ctx.triage is not None and ctx.triage.classification == "novel"
