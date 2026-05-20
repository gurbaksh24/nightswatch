"""Budget tracking for investigations.

Every investigation has a Budget. Stages call `assert_can_*` before doing
something expensive; the orchestrator's outermost loop catches
`BudgetExhausted` and triggers partial-result finalization.

Budgets are deliberately conservative defaults. Configure via settings.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ai_sre.exceptions import BudgetExhausted


@dataclass
class Budget:
    """Mutable budget bag, threaded through the pipeline."""

    max_wall_seconds: int = 300
    max_tool_calls: int = 30
    max_llm_tokens: int = 200_000
    max_llm_cost_usd: float = 0.50

    # Consumed
    started_monotonic: float = 0.0
    tool_calls_used: int = 0
    tokens_used: int = 0
    cost_used_usd: float = 0.0

    def start(self) -> None:
        self.started_monotonic = time.monotonic()

    @property
    def elapsed_seconds(self) -> float:
        if self.started_monotonic == 0.0:
            return 0.0
        return time.monotonic() - self.started_monotonic

    # ---- assertions ----
    def assert_within_wall(self) -> None:
        if self.elapsed_seconds >= self.max_wall_seconds:
            raise BudgetExhausted(
                "Wall-clock budget exhausted",
                details={"elapsed": self.elapsed_seconds, "max": self.max_wall_seconds},
            )

    def assert_can_call_tool(self) -> None:
        self.assert_within_wall()
        if self.tool_calls_used >= self.max_tool_calls:
            raise BudgetExhausted(
                "Tool-call budget exhausted",
                details={"used": self.tool_calls_used, "max": self.max_tool_calls},
            )

    def assert_can_call_llm(self, *, expected_tokens: int = 0) -> None:
        self.assert_within_wall()
        if self.tokens_used + expected_tokens > self.max_llm_tokens:
            raise BudgetExhausted(
                "LLM token budget exhausted",
                details={"used": self.tokens_used, "max": self.max_llm_tokens},
            )
        if self.cost_used_usd >= self.max_llm_cost_usd:
            raise BudgetExhausted(
                "LLM cost budget exhausted",
                details={"used_usd": self.cost_used_usd, "max_usd": self.max_llm_cost_usd},
            )

    # ---- recording ----
    def record_tool_call(self) -> None:
        self.tool_calls_used += 1

    def record_llm_call(self, *, tokens: int, cost_usd: float) -> None:
        self.tokens_used += tokens
        self.cost_used_usd += cost_usd

    def snapshot(self) -> dict[str, float | int]:
        return {
            "elapsed_seconds": self.elapsed_seconds,
            "tool_calls_used": self.tool_calls_used,
            "tokens_used": self.tokens_used,
            "cost_used_usd": self.cost_used_usd,
            "max_wall_seconds": self.max_wall_seconds,
            "max_tool_calls": self.max_tool_calls,
            "max_llm_tokens": self.max_llm_tokens,
            "max_llm_cost_usd": self.max_llm_cost_usd,
        }
