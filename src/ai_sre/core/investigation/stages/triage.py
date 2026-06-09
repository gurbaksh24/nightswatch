"""TriageStage — the first gate. Deterministic-only in this spec (no LLM).

Classifies the alert as `noise` | `known_issue` | `novel`:

- `noise`       → the same fingerprint has fired many times in a short window
                  (flapping). Counted from `alert` rows, since dedup collapses
                  the firings onto one investigation.
- `known_issue` → a prior investigation for the same fingerprint succeeded in
                  the last 24h; the report can lean on that conclusion.
- `novel`       → proceed with the full pipeline.

Dependencies are injected (the worker is the composition root). When they're
absent — e.g. ``Pipeline.default()`` for tests that don't touch the DB — the
stage degrades to `novel`.

See spec 0007.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ai_sre.core.investigation.context import (
    InvestigationContext,
    StageResult,
    TriageResult,
)

if TYPE_CHECKING:
    from ai_sre.core.alert.repository import AlertRepository
    from ai_sre.core.investigation.repository import InvestigationRepository

# Statuses that count as a usable prior conclusion for "known issue".
_RESOLVED_STATUSES = ("succeeded", "partial")


class TriageStage:
    name = "triage"
    timeout_seconds = 30
    max_tool_calls = 5

    def __init__(
        self,
        investigation_repo: InvestigationRepository | None = None,
        alert_repo: AlertRepository | None = None,
        *,
        known_issue_window: timedelta = timedelta(hours=24),
        noise_window: timedelta = timedelta(minutes=10),
        noise_threshold: int = 5,
    ) -> None:
        self.investigation_repo = investigation_repo
        self.alert_repo = alert_repo
        self.known_issue_window = known_issue_window
        self.noise_window = noise_window
        self.noise_threshold = noise_threshold

    async def execute(self, ctx: InvestigationContext) -> StageResult:
        ctx.budget.assert_within_wall()

        fingerprint = ctx.alert.get("fingerprint")
        triage = await self._classify(fingerprint, ctx)
        ctx.triage = triage
        return StageResult(
            name=self.name,
            status="succeeded",
            output={
                "classification": triage.classification,
                "related_investigation_id": (
                    str(triage.related_investigation_id)
                    if triage.related_investigation_id
                    else None
                ),
                "reasoning": triage.reasoning,
            },
        )

    async def _classify(
        self, fingerprint: str | None, ctx: InvestigationContext
    ) -> TriageResult:
        # No fingerprint or no DB access → can't do better than "novel".
        if not fingerprint:
            return TriageResult(classification="novel", reasoning="no fingerprint")

        now = datetime.now(UTC)

        # Noise: many alert firings for this fingerprint in a short window.
        if self.alert_repo is not None:
            flaps = await self.alert_repo.count_by_fingerprint(
                fingerprint, since=now - self.noise_window
            )
            if flaps >= self.noise_threshold:
                return TriageResult(
                    classification="noise",
                    reasoning=(
                        f"{flaps} firings in the last "
                        f"{int(self.noise_window.total_seconds() // 60)}m "
                        "(flapping)"
                    ),
                )

        # Known issue: a recent prior investigation for the same fingerprint
        # reached a conclusion.
        if self.investigation_repo is not None:
            prior = await self.investigation_repo.find_by_fingerprint(
                fingerprint,
                since=now - self.known_issue_window,
                exclude_id=ctx.investigation_id,
                statuses=_RESOLVED_STATUSES,
            )
            if prior:
                return TriageResult(
                    classification="known_issue",
                    related_investigation_id=prior[0].id,
                    reasoning=(
                        f"matches prior investigation {prior[0].id} "
                        f"({prior[0].status})"
                    ),
                )

        return TriageResult(classification="novel", reasoning="no prior match")
