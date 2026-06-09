"""InvestigationOrchestrator.

Single public method: ``run(investigation_id)``. Owns the lifecycle of one
investigation end-to-end:

    1. Load the investigation; return early if it's already terminal
       (idempotency) or missing.
    2. Build a fresh ``InvestigationContext`` (alert, service, budget) and
       restore which stages already completed (resume after a crash).
    3. Mark the investigation ``running``, then iterate the pipeline. Each
       stage runs under its own ``asyncio.wait_for`` timeout and persists an
       ``investigation_stage`` row.
    4. ``BudgetExhausted`` short-circuits to a partial finalize; any other
       stage error is recorded and the pipeline continues (the report stage
       handles missing inputs).
    5. Finalize the investigation status, then hand to delivery (a no-op
       until spec 0010).

The DB row is the source of truth, not the queue message: ``run`` is safe to
retry. Completed stages are read from ``investigation_stage`` and skipped.

See LLD §7.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from ai_sre.config import get_settings
from ai_sre.core.alert.repository import AlertRepository
from ai_sre.core.investigation.budget import Budget
from ai_sre.core.investigation.context import InvestigationContext
from ai_sre.core.investigation.repository import ToolCallRepository
from ai_sre.core.service.repository import ServiceRepository
from ai_sre.core.tenant.context import TenantContext
from ai_sre.exceptions import BudgetExhausted
from ai_sre.llm.tools import REGISTRY, ToolDispatcher
from ai_sre.models.alert import Alert
from ai_sre.models.service import Service
from ai_sre.models.tenant import Tenant
from ai_sre.utils.logging import get_logger

if TYPE_CHECKING:
    from ai_sre.core.investigation.pipeline import Pipeline, Stage
    from ai_sre.core.investigation.repository import InvestigationRepository
    from ai_sre.llm.gateway import LLMGateway

logger = get_logger(__name__)

# Once an investigation reaches one of these it's done; re-running is a no-op.
_TERMINAL_STATUSES = frozenset({"succeeded", "partial", "failed"})

# Placeholder api-key id for the worker-built TenantContext: investigations
# run outside any HTTP request, so there's no real API key behind them.
_WORKER_API_KEY_ID = UUID(int=0)


def _alert_to_dict(alert: Alert | None) -> dict[str, Any]:
    if alert is None:
        return {}
    return {
        "id": str(alert.id),
        "alert_name": alert.alert_name,
        "fingerprint": alert.fingerprint,
        "severity": alert.severity,
        "status": alert.status,
        "labels": alert.labels,
        "annotations": alert.annotations,
    }


def _service_to_dict(service: Service | None) -> dict[str, Any]:
    if service is None:
        return {}
    return {
        "id": str(service.id),
        "name": service.name,
        "label_selector": service.label_selector,
    }


class InvestigationOrchestrator:
    """Runs one investigation through the pipeline. Idempotent / resumable."""

    def __init__(
        self,
        pipeline: Pipeline,
        repo: InvestigationRepository,
        gateway: LLMGateway | None = None,
        # delivery: DeliveryDispatcher,   # added in spec 0010
    ) -> None:
        self.pipeline = pipeline
        self.repo = repo
        # Injected so the LLM stages (0009+) can chat()/tool_loop(). Stubs
        # don't use it yet; may be None when no provider key is configured.
        self.gateway = gateway

    async def run(self, investigation_id: UUID) -> None:
        """Run the full pipeline for a single investigation. Safe to retry."""
        log = logger.bind(investigation_id=str(investigation_id))
        inv = await self.repo.get(investigation_id)
        if inv is None:
            log.error("orchestrator.investigation_not_found")
            return
        if inv.status in _TERMINAL_STATUSES:
            log.info("orchestrator.already_terminal", status=inv.status)
            return

        log.info("orchestrator.start")
        ctx = await self._build_context(inv)
        await self.repo.set_status(inv, status="running", started_at=datetime.now(UTC))

        try:
            for stage in self.pipeline.stages:
                if ctx.has_completed(stage.name):
                    log.info("orchestrator.skip_completed", stage=stage.name)
                    continue
                await self._run_stage(stage, ctx)
        except BudgetExhausted as exc:
            log.warning("orchestrator.budget_exhausted", reason=str(exc))
            await self._finalize_partial(inv, ctx, reason=str(exc))
        else:
            await self._finalize(inv, ctx)

        await self._dispatch_delivery(ctx)
        log.info("orchestrator.done", status=inv.status)

    # ---- context ----

    async def _build_context(self, inv: Any) -> InvestigationContext:
        session = self.repo.session
        tenant_id = self.repo.tenant_id

        alert = (
            await AlertRepository(session, tenant_id).get(inv.triggering_alert_id)
            if inv.triggering_alert_id is not None
            else None
        )
        service = await ServiceRepository(session, tenant_id).get_by_id(inv.service_id)
        tenant_row = await session.get(Tenant, tenant_id)

        tenant_ctx = TenantContext(
            tenant_id=tenant_id,
            name=tenant_row.name if tenant_row else "",
            slug=tenant_row.slug if tenant_row else "",
            api_key_id=_WORKER_API_KEY_ID,
        )

        settings = get_settings()
        budget = Budget(
            max_wall_seconds=settings.inv_budget_wall_seconds,
            max_tool_calls=settings.inv_budget_tool_calls,
            max_llm_tokens=settings.llm_max_tokens_per_investigation,
            max_llm_cost_usd=settings.llm_max_cost_usd_per_investigation,
        )
        budget.start()

        ctx = InvestigationContext(
            tenant=tenant_ctx,
            investigation_id=inv.id,
            alert=_alert_to_dict(alert),
            service=_service_to_dict(service),
            dependencies={},
            metric_catalog={},
            budget=budget,
        )
        # Wire the LLM collaborators the stages will use (0009+). The
        # dispatcher persists tool_call rows via a tenant-scoped repository.
        ctx.gateway = self.gateway
        ctx.dispatcher = ToolDispatcher(REGISTRY, ToolCallRepository(session, tenant_id))

        # Restore completed stages for idempotent resume.
        for name in await self.repo.get_completed_stage_names(inv.id):
            ctx.mark_completed(name)
        return ctx

    # ---- stage execution ----

    async def _run_stage(self, stage: Stage, ctx: InvestigationContext) -> None:
        log = logger.bind(investigation_id=str(ctx.investigation_id), stage=stage.name)
        started = datetime.now(UTC)
        log.info("stage.start")
        # Persist a `running` row up front so tool calls made during the stage
        # can reference its id (tool_call.stage_id). It's updated to the final
        # status below. Only `succeeded` rows count for resume, so a leftover
        # `running` row is simply re-run.
        running = await self.repo.upsert_stage(
            ctx.investigation_id, name=stage.name, status="running", started_at=started
        )
        ctx.current_stage_id = running.id
        try:
            result = await asyncio.wait_for(
                stage.execute(ctx), timeout=stage.timeout_seconds
            )
        except TimeoutError:
            log.warning("stage.timeout")
            await self.repo.upsert_stage(
                ctx.investigation_id,
                name=stage.name,
                status="timed_out",
                error={"type": "timeout", "timeout_seconds": stage.timeout_seconds},
                started_at=started,
                completed_at=datetime.now(UTC),
            )
            return
        except BudgetExhausted:
            await self.repo.upsert_stage(
                ctx.investigation_id,
                name=stage.name,
                status="budget_exhausted",
                started_at=started,
                completed_at=datetime.now(UTC),
            )
            raise
        except Exception as exc:
            # Record the failure and continue; the report stage handles
            # missing inputs (one bad stage shouldn't sink the investigation).
            log.exception("stage.error", error=str(exc))
            await self.repo.upsert_stage(
                ctx.investigation_id,
                name=stage.name,
                status="failed",
                error={"type": type(exc).__name__, "message": str(exc)},
                started_at=started,
                completed_at=datetime.now(UTC),
            )
            return

        if result.status == "succeeded":
            ctx.mark_completed(stage.name)
        await self.repo.upsert_stage(
            ctx.investigation_id,
            name=stage.name,
            status=result.status,
            output=result.output,
            error=result.error,
            started_at=started,
            completed_at=datetime.now(UTC),
        )
        log.info("stage.complete", status=result.status)

    # ---- finalization ----

    async def _finalize(self, inv: Any, ctx: InvestigationContext) -> None:
        confidence = ctx.report.confidence if ctx.report else "low"
        await self.repo.set_status(
            inv,
            status="succeeded",
            confidence=confidence,
            completed_at=datetime.now(UTC),
            budget_snapshot=ctx.budget.snapshot(),
        )

    async def _finalize_partial(
        self, inv: Any, ctx: InvestigationContext, *, reason: str
    ) -> None:
        now = datetime.now(UTC)
        # Stages we never got to (or that were interrupted) are budget_exhausted.
        for stage in self.pipeline.stages:
            if not ctx.has_completed(stage.name):
                await self.repo.upsert_stage(
                    ctx.investigation_id,
                    name=stage.name,
                    status="budget_exhausted",
                    error={"reason": reason},
                    started_at=now,
                    completed_at=now,
                )
        confidence = ctx.report.confidence if ctx.report else "low"
        await self.repo.set_status(
            inv,
            status="partial",
            confidence=confidence,
            completed_at=now,
            budget_snapshot=ctx.budget.snapshot(),
        )

    async def _dispatch_delivery(self, ctx: InvestigationContext) -> None:
        # Delivery (Slack) lands in spec 0010; nothing to do yet.
        logger.info(
            "orchestrator.delivery_skipped",
            investigation_id=str(ctx.investigation_id),
        )
