"""Unit tests for SlackDelivery.handle_callback (spec 0013).

Pure parsing — block_actions payload -> CallbackResult. No network/DB.
"""

from __future__ import annotations

import pytest

from ai_sre.delivery.slack import SlackDelivery


def _block_actions(action_id: str, value: str = "inv-1") -> dict:
    return {
        "type": "block_actions",
        "user": {"id": "U1", "username": "alice"},
        "actions": [{"action_id": action_id, "value": value, "type": "button"}],
    }


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_id", "kind"),
    [
        ("feedback_useful", "useful"),
        ("feedback_not_useful", "not_useful"),
        ("feedback_actual_cause", "actual_cause"),
    ],
)
async def test_button_maps_to_feedback_kind(action_id: str, kind: str) -> None:
    result = await SlackDelivery().handle_callback(_block_actions(action_id))
    assert result.kind == "feedback"
    assert result.payload["feedback_kind"] == kind
    assert result.payload["investigation_id"] == "inv-1"
    assert result.payload["actor"] == "alice"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_action_id_is_ignored() -> None:
    result = await SlackDelivery().handle_callback(_block_actions("something_else"))
    assert result.kind == "ignore"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_block_actions_is_ignored() -> None:
    result = await SlackDelivery().handle_callback({"type": "view_submission"})
    assert result.kind == "ignore"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_actions_is_ignored() -> None:
    result = await SlackDelivery().handle_callback(
        {"type": "block_actions", "actions": []}
    )
    assert result.kind == "ignore"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_actor_falls_back_to_user_id() -> None:
    payload = _block_actions("feedback_useful")
    payload["user"] = {"id": "U99"}  # no username
    result = await SlackDelivery().handle_callback(payload)
    assert result.payload["actor"] == "U99"
