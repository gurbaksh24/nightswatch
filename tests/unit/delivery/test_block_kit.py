"""Unit tests for the Slack Block Kit formatter (spec 0010)."""

from __future__ import annotations

from typing import Any

import pytest

from ai_sre.core.investigation.context import Report
from ai_sre.delivery.slack.block_kit import build_blocks


def _report(
    hypotheses: list[dict[str, Any]], next_actions: list[dict[str, Any]] | None = None,
    *, headline: str = "DB connection pool exhausted", confidence: str = "high",
) -> Report:
    return Report(
        headline=headline,
        confidence=confidence,
        hypotheses=hypotheses,
        next_actions=next_actions or [],
    )


def _sections(blocks: list[dict[str, Any]]) -> list[str]:
    return [b["text"]["text"] for b in blocks if b["type"] == "section"]


def _hyp_sections(blocks: list[dict[str, Any]]) -> list[str]:
    return [s for s in _sections(blocks) if s.startswith("*Hypothesis")]


@pytest.mark.unit
def test_header_and_actions_always_present() -> None:
    blocks = build_blocks(_report([]))
    assert blocks[0]["type"] == "header"
    assert any(b["type"] == "actions" for b in blocks)
    # three feedback buttons (inert in MVP)
    actions = next(b for b in blocks if b["type"] == "actions")
    assert {e["action_id"] for e in actions["elements"]} == {
        "feedback_useful", "feedback_not_useful", "feedback_actual_cause"
    }


@pytest.mark.unit
def test_buttons_carry_investigation_id_as_value() -> None:
    report = Report(headline="x", investigation_id="abc-123")
    actions = next(b for b in build_blocks(report) if b["type"] == "actions")
    assert {e["value"] for e in actions["elements"]} == {"abc-123"}


@pytest.mark.unit
def test_buttons_value_falls_back_when_no_investigation_id() -> None:
    actions = next(b for b in build_blocks(Report(headline="x")) if b["type"] == "actions")
    # `value` must be a non-empty string for Slack; we fall back to a sentinel.
    assert all(e["value"] for e in actions["elements"])


@pytest.mark.unit
def test_zero_hypotheses_renders_placeholder() -> None:
    blocks = build_blocks(_report([]))
    assert _hyp_sections(blocks) == []
    assert any("No hypotheses" in s for s in _sections(blocks))


@pytest.mark.unit
def test_one_and_three_hypotheses() -> None:
    assert len(_hyp_sections(build_blocks(_report([{"statement": "H1"}])))) == 1
    three = [{"statement": f"H{i}"} for i in range(3)]
    assert len(_hyp_sections(build_blocks(_report(three)))) == 3


@pytest.mark.unit
def test_hypotheses_capped_at_three() -> None:
    five = [{"statement": f"H{i}"} for i in range(5)]
    assert len(_hyp_sections(build_blocks(_report(five)))) == 3


@pytest.mark.unit
def test_next_actions_optional() -> None:
    without = build_blocks(_report([{"statement": "H"}]))
    assert not any("next actions" in s.lower() for s in _sections(without))
    with_actions = build_blocks(
        _report([{"statement": "H"}], [{"action": "Scale the pool"}])
    )
    assert any("Scale the pool" in s for s in _sections(with_actions))


@pytest.mark.unit
def test_confidence_badge_and_header_truncation() -> None:
    blocks = build_blocks(_report([], headline="x" * 300, confidence="low"))
    assert len(blocks[0]["text"]["text"]) <= 150
    assert any("🔴 low" in s for s in _sections(blocks))
