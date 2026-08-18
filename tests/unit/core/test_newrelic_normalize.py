"""Unit tests for NewRelicWebhookPayload normalization (spec 0020)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ai_sre.schemas.alert import NewRelicWebhookPayload

pytestmark = pytest.mark.unit


def _payload(**overrides: object) -> NewRelicWebhookPayload:
    base: dict[str, object] = {
        "issue_id": "abc-123",
        "title": "Error rate above 5%",
        "state": "ACTIVATED",
        "priority": "CRITICAL",
        "condition_name": "checkout error rate",
        "policy_name": "mfs-onchain",
        "entity_names": ["checkout", "checkout-canary"],
        "nrql": "SELECT count(*) FROM TransactionError",
        "route_group": "onchain",
        "runbook_url": "https://runbooks.example/checkout",
        "started_at": datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return NewRelicWebhookPayload.model_validate(base)


def test_normalization_layout() -> None:
    am = _payload().to_alertmanager()
    assert len(am.alerts) == 1
    alert = am.alerts[0]
    assert alert.labels == {
        "alertname": "checkout error rate",
        "severity": "critical",
        "policy": "mfs-onchain",
        "route_group": "onchain",
        "entity": "checkout",
    }
    assert alert.annotations["summary"] == "Error rate above 5%"
    assert alert.annotations["nrql"] == "SELECT count(*) FROM TransactionError"
    assert alert.annotations["runbook_url"] == "https://runbooks.example/checkout"
    assert alert.annotations["other_entities"] == "checkout-canary"
    # issue_id is per-occurrence → annotations only, never labels (dedupe).
    assert alert.annotations["issue_id"] == "abc-123"
    assert "issue_id" not in alert.labels
    assert alert.status == "firing" and am.status == "firing"
    assert alert.startsAt == datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("priority", "severity"),
    [("CRITICAL", "critical"), ("high", "high"), ("Sev1", "sev1"), (None, None)],
)
def test_priority_to_severity(priority: str | None, severity: str | None) -> None:
    alert = _payload(priority=priority).to_alertmanager().alerts[0]
    assert alert.labels.get("severity") == severity


def test_computed_keys_win_over_template_extras() -> None:
    alert = _payload(labels={"alertname": "spoofed", "team": "onchain"}).to_alertmanager().alerts[0]
    assert alert.labels["alertname"] == "checkout error rate"
    assert alert.labels["team"] == "onchain"


@pytest.mark.parametrize(
    ("state", "is_open"),
    [
        ("ACTIVATED", True),
        ("created", True),
        ("OPEN", True),
        ("CLOSED", False),
        ("DEACTIVATED", False),
    ],
)
def test_is_open_states(state: str, is_open: bool) -> None:
    assert _payload(state=state).is_open is is_open


def test_missing_started_at_defaults_to_now() -> None:
    alert = _payload(started_at=None).to_alertmanager().alerts[0]
    assert alert.startsAt.tzinfo is not None
