"""Pipeline and Stage protocol.

The pipeline is just an ordered list of `Stage` implementations. `Stage` is a
runtime-checkable Protocol; any class with `name`, `timeout_seconds`,
`max_tool_calls` attributes and an `async execute(ctx)` method qualifies.

To change the default order or add a stage, edit `Pipeline.default()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ai_sre.core.investigation.context import InvestigationContext, StageResult


@runtime_checkable
class Stage(Protocol):
    """Contract for a pipeline stage."""

    name: str
    timeout_seconds: int
    max_tool_calls: int

    async def execute(self, ctx: InvestigationContext) -> StageResult: ...


@dataclass(frozen=True)
class Pipeline:
    """Ordered list of stages, plus a factory for the default arrangement."""

    stages: tuple[Stage, ...]

    @classmethod
    def default(cls) -> Pipeline:
        """The canonical MVP pipeline. Order matters."""
        # Imports here to avoid a circular dep at module load time.
        from ai_sre.core.investigation.stages.context_assembly import (
            ContextAssemblyStage,
        )
        from ai_sre.core.investigation.stages.hypothesis import HypothesisStage
        from ai_sre.core.investigation.stages.report import ReportStage
        from ai_sre.core.investigation.stages.triage import TriageStage
        from ai_sre.core.investigation.stages.validation import ValidationStage

        return cls(
            stages=(
                TriageStage(),
                ContextAssemblyStage(),
                HypothesisStage(),
                ValidationStage(),
                ReportStage(),
            )
        )
