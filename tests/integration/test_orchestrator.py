"""End-to-end orchestrator tests (spec 0007).

The happy path drives the worker entrypoint (`run_investigation`) so the
bootstrap + default pipeline are exercised. The behaviour tests (resume,
timeout, budget) construct the orchestrator directly with small custom
pipelines, since `_build_context` needs a real tenant/service/alert.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_sre.core.alert.repository import AlertRepository
from ai_sre.core.investigation.context import InvestigationContext, StageResult
from ai_sre.core.investigation.orchestrator import InvestigationOrchestrator
from ai_sre.core.investigation.pipeline import Pipeline
from ai_sre.core.investigation.repository import InvestigationRepository
from ai_sre.core.service.repository import ServiceRepository
from ai_sre.core.tenant.repository import TenantRepository
from ai_sre.core.tenant.service import TenantService
from ai_sre.db import session_scope
from ai_sre.exceptions import BudgetExhausted
from ai_sre.models.investigation import Investigation, InvestigationStage
from ai_sre.workers.app import run_investigation

_STAGE_NAMES = {"triage", "context_assembly", "hypothesis", "validation", "report"}


async def _seed(*, slug: str, fingerprint: str = "fp-1") -> tuple[UUID, UUID]:
    """Create tenant + service + alert + pending investigation.
    Returns (tenant_id, investigation_id)."""
    now = datetime.now(UTC)
    async with session_scope() as session:
        tenant = await TenantService(TenantRepository(session)).create(
            name=slug, slug=slug
        )
        service = await ServiceRepository(session, tenant.id).create(
            name="checkout", label_selector={"app": "checkout"}, ownership=None, slo_config=None
        )
        alert = await AlertRepository(session, tenant.id).create(
            source="alertmanager",
            received_at=now,
            raw_payload={},
            alert_name="HighErrorRate",
            severity="critical",
            status="firing",
            started_at=now,
            ended_at=None,
            labels={"alertname": "HighErrorRate"},
            annotations={},
            fingerprint=fingerprint,
        )
        inv = await InvestigationRepository(session, tenant.id).create_pending(
            service_id=service.id, triggering_alert_id=alert.id, fingerprint=fingerprint
        )
    return tenant.id, inv.id


async def _stages(investigation_id: UUID) -> dict[str, str]:
    """Return {stage_name: status} for an investigation."""
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(InvestigationStage).where(
                    InvestigationStage.investigation_id == investigation_id
                )
            )
        ).scalars().all()
    return {r.name: r.status for r in rows}


async def _status(investigation_id: UUID) -> str:
    async with session_scope() as session:
        inv = await session.get(Investigation, investigation_id)
        assert inv is not None
        return inv.status


# A tiny configurable stage for the behaviour tests.
class _Stage:
    max_tool_calls = 0

    def __init__(
        self,
        name: str,
        calls: list[str] | None = None,
        *,
        sleep: float = 0.0,
        raises: Exception | None = None,
        timeout_seconds: int = 5,
    ) -> None:
        self.name = name
        self.timeout_seconds = timeout_seconds
        self._calls = calls
        self._sleep = sleep
        self._raises = raises

    async def execute(self, ctx: InvestigationContext) -> StageResult:
        if self._calls is not None:
            self._calls.append(self.name)
        if self._sleep:
            await asyncio.sleep(self._sleep)
        if self._raises is not None:
            raise self._raises
        return StageResult(name=self.name, status="succeeded")


async def _run_custom(tenant_id: UUID, inv_id: UUID, pipeline: Pipeline) -> None:
    async with session_scope() as session:
        repo = InvestigationRepository(session, tenant_id)
        orch = InvestigationOrchestrator(pipeline, repo)
        await orch.run(inv_id)
        await session.commit()


# ---- happy path (worker entrypoint + default pipeline) ----


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_run_succeeds(db_engine: AsyncEngine) -> None:
    _tenant_id, inv_id = await _seed(slug="acme")
    await run_investigation(str(inv_id))

    assert await _status(inv_id) == "succeeded"
    stages = await _stages(inv_id)
    assert set(stages) == _STAGE_NAMES
    assert all(status == "succeeded" for status in stages.values())


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rerun_when_terminal_is_noop(db_engine: AsyncEngine) -> None:
    _tenant_id, inv_id = await _seed(slug="acme")
    await run_investigation(str(inv_id))
    await run_investigation(str(inv_id))  # already succeeded -> early return

    assert await _status(inv_id) == "succeeded"
    # upsert (not duplicate insert): still exactly five stage rows.
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(InvestigationStage).where(
                    InvestigationStage.investigation_id == inv_id
                )
            )
        ).scalars().all()
    assert len(rows) == 5


@pytest.mark.integration
@pytest.mark.asyncio
async def test_missing_investigation_is_safe(db_engine: AsyncEngine) -> None:
    from ai_sre.utils.ids import new_id

    await run_investigation(str(new_id()))  # must not raise


# ---- behaviour tests (custom pipelines) ----


@pytest.mark.integration
@pytest.mark.asyncio
async def test_resume_skips_completed_stage(db_engine: AsyncEngine) -> None:
    tenant_id, inv_id = await _seed(slug="acme")
    now = datetime.now(UTC)
    # Pre-record 'triage' as already succeeded (as if a prior worker ran it).
    async with session_scope() as session:
        await InvestigationRepository(session, tenant_id).upsert_stage(
            inv_id, name="triage", status="succeeded", started_at=now, completed_at=now
        )

    calls: list[str] = []
    pipeline = Pipeline(stages=(_Stage("triage", calls), _Stage("report", calls)))
    await _run_custom(tenant_id, inv_id, pipeline)

    assert calls == ["report"]  # triage was skipped on resume
    assert await _status(inv_id) == "succeeded"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stage_timeout_is_recorded_and_pipeline_continues(
    db_engine: AsyncEngine,
) -> None:
    tenant_id, inv_id = await _seed(slug="acme")
    pipeline = Pipeline(
        stages=(
            _Stage("triage", sleep=0.3, timeout_seconds=0),  # will time out
            _Stage("report"),
        )
    )
    await _run_custom(tenant_id, inv_id, pipeline)

    stages = await _stages(inv_id)
    assert stages["triage"] == "timed_out"
    assert stages["report"] == "succeeded"
    assert await _status(inv_id) == "succeeded"  # timeout doesn't sink the run


@pytest.mark.integration
@pytest.mark.asyncio
async def test_budget_exhausted_finalizes_partial(db_engine: AsyncEngine) -> None:
    tenant_id, inv_id = await _seed(slug="acme")
    calls: list[str] = []
    pipeline = Pipeline(
        stages=(
            _Stage("triage", calls, raises=BudgetExhausted("cap")),
            _Stage("report", calls),
        )
    )
    await _run_custom(tenant_id, inv_id, pipeline)

    assert await _status(inv_id) == "partial"
    stages = await _stages(inv_id)
    assert stages["triage"] == "budget_exhausted"
    assert stages["report"] == "budget_exhausted"  # never ran
    assert "report" not in calls


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stage_failure_is_recorded_and_pipeline_continues(
    db_engine: AsyncEngine,
) -> None:
    tenant_id, inv_id = await _seed(slug="acme")
    pipeline = Pipeline(
        stages=(
            _Stage("triage", raises=RuntimeError("boom")),
            _Stage("report"),
        )
    )
    await _run_custom(tenant_id, inv_id, pipeline)

    stages = await _stages(inv_id)
    assert stages["triage"] == "failed"
    assert stages["report"] == "succeeded"
    assert await _status(inv_id) == "succeeded"
