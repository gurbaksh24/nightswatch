"""InvestigationOrchestrator.

Single public method: `run(investigation_id)`. Owns the lifecycle of one
investigation end-to-end:

    1. Load all the context (alert, service, topology, catalog).
    2. Build a fresh `InvestigationContext`, start the budget.
    3. Iterate stages from the pipeline. Each stage gets the context.
    4. Persist stage outcome (succeeded / failed / timed_out / budget_exhausted).
    5. On `BudgetExhausted`, jump to finalize-with-partial-results.
    6. On any other exception in a stage, persist failure but continue to the
       Report stage (which knows how to handle missing inputs).
    7. After all stages, persist the final Report.
    8. Hand to DeliveryDispatcher.

Idempotency: each call to `run` is safe to retry. Stages check
`ctx.has_completed(stage.name)` and re-load completed outputs from the DB.

See LLD §7.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import UUID

from ai_sre.exceptions import BudgetExhausted
from ai_sre.utils.logging import get_logger

if TYPE_CHECKING:
    from ai_sre.core.investigation.context import InvestigationContext
    from ai_sre.core.investigation.pipeline import Pipeline, Stage

logger = get_logger(__name__)


class InvestigationOrchestrator:
    def __init__(
        self,
        pipeline: Pipeline,
        # repo: InvestigationRepository,
        # delivery: DeliveryDispatcher,
    ) -> None:
        self.pipeline = pipeline
        # self.repo = repo
        # self.delivery = delivery

    async def run(self, investigation_id: UUID) -> None:
        """Run the full pipeline for a single investigation.

        Implementation tracked by spec-NNNN (investigation-orchestrator).
        """
        log = logger.bind(investigation_id=str(investigation_id))
        log.info("orchestrator.start")
        ctx = await self._build_context(investigation_id)

        try:
            for stage in self.pipeline.stages:
                if ctx.has_completed(stage.name):
                    log.info("orchestrator.skip_completed", stage=stage.name)
                    continue
                await self._run_stage(stage, ctx)
        except BudgetExhausted as e:
            log.warning("orchestrator.budget_exhausted", reason=str(e))
            await self._finalize_partial(ctx, reason=str(e))
        else:
            await self._finalize(ctx)

        await self._dispatch_delivery(ctx)

    # ----- helpers (stubs) -----

    async def _build_context(self, investigation_id: UUID) -> InvestigationContext:
        # TODO(spec-NNNN: investigation-orchestrator)
        raise NotImplementedError

    async def _run_stage(self, stage: Stage, ctx: InvestigationContext) -> None:
        log = logger.bind(stage=stage.name)
        log.info("stage.start")
        # await self.repo.mark_stage_running(ctx.investigation_id, stage.name)
        try:
            result = await asyncio.wait_for(
                stage.execute(ctx), timeout=stage.timeout_seconds
            )
            ctx.mark_completed(stage.name)
            log.info("stage.complete", status=result.status)
            # await self.repo.persist_stage(result)
        except asyncio.TimeoutError:
            log.warning("stage.timeout")
            # await self.repo.mark_stage_timed_out(ctx.investigation_id, stage.name)
        except BudgetExhausted:
            raise
        except Exception as e:                            # noqa: BLE001
            log.exception("stage.error", error=str(e))
            # await self.repo.mark_stage_failed(...)
            # Continue to next stage; Report stage handles missing inputs.

    async def _finalize(self, ctx: InvestigationContext) -> None:
        # TODO: persist Report, mark investigation succeeded.
        raise NotImplementedError

    async def _finalize_partial(self, ctx: InvestigationContext, *, reason: str) -> None:
        # TODO: persist whatever Report we have with confidence=low and status=partial.
        raise NotImplementedError

    async def _dispatch_delivery(self, ctx: InvestigationContext) -> None:
        # TODO: hand to DeliveryDispatcher.
        raise NotImplementedError
