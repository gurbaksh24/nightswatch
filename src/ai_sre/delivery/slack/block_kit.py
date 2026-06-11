"""Slack Block Kit formatter for an investigation report.

Pure function: Report -> list[block dict]. Robust to 0..N hypotheses and
missing next-actions. Stays under Slack's limits (header <=150 chars, section
text <=3000) by truncating. The action buttons are present but inert in MVP —
spec 0013 wires their callbacks.

See LLD §14.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ai_sre.core.investigation.context import Report

_CONFIDENCE_BADGE = {"high": "🟢 high", "medium": "🟡 medium", "low": "🔴 low"}
_MAX_HEADER = 150
_MAX_SECTION = 3000
_MAX_HYPOTHESES = 3


def _trunc(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _statement(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("statement", item))
    return str(item)


def _action_text(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("action") or item.get("statement") or item)
    return str(item)


def build_blocks(report: Report) -> list[dict[str, Any]]:
    """Build the Block Kit blocks for a report."""
    headline = report.headline or "AI-SRE diagnosis"
    badge = _CONFIDENCE_BADGE.get(report.confidence, "⚪ unknown")

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": _trunc(f"🚨 {headline}", _MAX_HEADER)},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Confidence:* {badge}"},
        },
    ]

    hypotheses = report.hypotheses or []
    if hypotheses:
        blocks.append({"type": "divider"})
        for i, h in enumerate(hypotheses[:_MAX_HYPOTHESES], start=1):
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _trunc(f"*Hypothesis {i}.* {_statement(h)}", _MAX_SECTION),
                    },
                }
            )
    else:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "_No hypotheses were produced._"},
            }
        )

    actions = report.next_actions or []
    if actions:
        bullets = "\n".join(f"• {_action_text(a)}" for a in actions[:10])
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _trunc(f"*Suggested next actions:*\n{bullets}", _MAX_SECTION),
                },
            }
        )

    # Each button carries the investigation_id as its `value` so the callback
    # receiver (api/delivery.py) can attribute the feedback without server-side
    # state. `value` must be 1..2000 chars; fall back to "unknown" when unset.
    inv_value = report.investigation_id or "unknown"
    blocks.append(
        {
            "type": "actions",
            "block_id": "ai_sre_feedback",
            "elements": [
                {"type": "button", "action_id": "feedback_useful",
                 "value": inv_value,
                 "text": {"type": "plain_text", "text": "👍 Useful"}},
                {"type": "button", "action_id": "feedback_not_useful",
                 "value": inv_value,
                 "text": {"type": "plain_text", "text": "👎 Not useful"}},
                {"type": "button", "action_id": "feedback_actual_cause",
                 "value": inv_value,
                 "text": {"type": "plain_text", "text": "🎯 Actual cause"}},
            ],
        }
    )
    return blocks
