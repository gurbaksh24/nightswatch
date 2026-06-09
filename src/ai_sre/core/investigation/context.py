"""InvestigationContext: the typed bag threaded through pipeline stages.

Read-mostly fields are populated at orchestrator startup (tenant, alert,
service, topology, catalog). Stage outputs are filled in order.

A stage:
    1. Reads what it needs.
    2. Mutates the budget.
    3. Writes its result into the appropriate field.
    4. Returns a `StageResult` to the orchestrator.

Tool-call records are appended as they happen (by the LLM gateway via the
tool dispatcher).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from ai_sre.api.deps import TenantContext
    from ai_sre.core.investigation.budget import Budget
    from ai_sre.llm.gateway import LLMGateway
    from ai_sre.llm.tools import ToolDispatcher


# ---- Stage output types (loose for now; tighten per stage) ----


@dataclass
class TriageResult:
    classification: str           # "noise" | "known_issue" | "novel"
    related_investigation_id: UUID | None = None
    reasoning: str = ""


@dataclass
class ContextSummary:
    """Output of ContextAssemblyStage.

    Parallel-fetched topology, recent deploys, error rates, etc.
    The hypothesis stage consumes this directly.
    """

    recent_metrics: dict[str, Any] = field(default_factory=dict)
    recent_changes: list[dict[str, Any]] = field(default_factory=list)
    dependency_health: dict[str, Any] = field(default_factory=dict)


@dataclass
class Hypothesis:
    statement: str
    evidence_refs: list[str] = field(default_factory=list)
    initial_confidence: float = 0.5


@dataclass
class ValidatedHypothesis:
    hypothesis: Hypothesis
    confidence: float
    supporting_evidence: list[dict[str, Any]] = field(default_factory=list)
    refuting_evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Report:
    """Final structured RCA. Persisted as `report` row."""

    schema_version: str = "1"
    headline: str = ""
    confidence: str = "low"       # low | medium | high
    hypotheses: list[dict[str, Any]] = field(default_factory=list)
    next_actions: list[dict[str, Any]] = field(default_factory=list)
    related_incidents: list[dict[str, Any]] = field(default_factory=list)
    prompt_version: str = ""
    code_version: str = ""


@dataclass
class ToolCallRecord:
    tool_name: str
    stage: str
    input: dict[str, Any]
    output: dict[str, Any]
    latency_ms: int
    outcome: str                  # success | error | timeout | budget_blocked
    error: str | None = None
    occurred_at: datetime | None = None


@dataclass
class StageResult:
    """Returned by a stage's `execute(ctx)`. The orchestrator persists this."""

    name: str
    status: str                   # succeeded | failed | timed_out | budget_exhausted
    output: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None


# ---- The context object ----


@dataclass
class InvestigationContext:
    """Read-mostly identity + mutable stage outputs + audit trail."""

    # Identity
    tenant: TenantContext
    investigation_id: UUID
    alert: dict[str, Any]         # normalised alert (Pydantic NormalisedAlert)
    service: dict[str, Any]       # subject service record
    dependencies: dict[str, Any]  # topology
    metric_catalog: dict[str, Any]
    budget: Budget

    # Stage outputs (filled in order)
    triage: TriageResult | None = None
    context: ContextSummary | None = None
    hypotheses: list[Hypothesis] = field(default_factory=list)
    validated: list[ValidatedHypothesis] = field(default_factory=list)
    report: Report | None = None

    # Audit trail (append-only from stages / tool dispatcher)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    completed_stages: set[str] = field(default_factory=set)

    # Injected collaborators + per-stage cursor. Set by the orchestrator; the
    # LLM stages (0009+) read `gateway`/`dispatcher`, and the dispatcher uses
    # `current_stage_id` to attribute tool_call rows to the running stage.
    current_stage_id: UUID | None = None
    gateway: LLMGateway | None = None
    dispatcher: ToolDispatcher | None = None

    def has_completed(self, stage_name: str) -> bool:
        return stage_name in self.completed_stages

    def mark_completed(self, stage_name: str) -> None:
        self.completed_stages.add(stage_name)
