"""ContextAssemblyStage.

Deterministic data collection. Fans out connector calls in parallel. NO LLM.

Pulls:
    * Recent metrics for the subject service (error rate, latency, saturation
      proxies) over a configurable window before the alert started.
    * Recent change events (deploys, config changes) for the service and its
      immediate dependencies — stubbed pluggable interface for MVP.
    * Health snapshots of immediate dependencies (upstream + downstream).

Output: `ctx.context: ContextSummary`. The Hypothesis stage consumes this
to ground its initial reasoning.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ai_sre.core.investigation.context import (
    ContextSummary,
    InvestigationContext,
    StageResult,
)


class ContextAssemblyStage:
    name = "context_assembly"
    timeout_seconds = 60
    max_tool_calls = 10   # not LLM tool calls — connector queries directly

    async def execute(self, ctx: InvestigationContext) -> StageResult:
        ctx.budget.assert_within_wall()

        # TODO(spec-NNNN: context-assembly):
        # async gather:
        #   - connector.query(recent_metrics_for(ctx.service))
        #   - connector.query(dependency_health_for(ctx.dependencies))
        #   - change_feed.list_recent(ctx.service, window)
        async def _stub() -> dict[str, Any]:
            await asyncio.sleep(0)
            return {}

        _ = await asyncio.gather(_stub(), _stub(), _stub())

        ctx.context = ContextSummary()
        return StageResult(name=self.name, status="succeeded")
