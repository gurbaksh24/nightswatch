"""Unit tests for the deterministic alert fingerprinter (spec 0006)."""

from __future__ import annotations

import pytest

from ai_sre.core.alert.fingerprinter import fingerprint
from ai_sre.utils.ids import new_id

TENANT = new_id()


@pytest.mark.unit
def test_fingerprint_is_deterministic() -> None:
    a = fingerprint(TENANT, "HighErrorRate", {"service": "checkout"}, "critical")
    b = fingerprint(TENANT, "HighErrorRate", {"service": "checkout"}, "critical")
    assert a == b
    assert len(a) == 64  # sha256 hex


@pytest.mark.unit
def test_label_order_does_not_matter() -> None:
    a = fingerprint(TENANT, "X", {"a": "1", "b": "2"}, None)
    b = fingerprint(TENANT, "X", {"b": "2", "a": "1"}, None)
    assert a == b


@pytest.mark.unit
def test_unstable_labels_are_excluded() -> None:
    """Same alert on different pods → same fingerprint."""
    a = fingerprint(TENANT, "X", {"service": "checkout", "pod": "pod-1"}, "warning")
    b = fingerprint(TENANT, "X", {"service": "checkout", "pod": "pod-2"}, "warning")
    assert a == b


@pytest.mark.unit
def test_stable_label_change_changes_fingerprint() -> None:
    a = fingerprint(TENANT, "X", {"service": "checkout"}, None)
    b = fingerprint(TENANT, "X", {"service": "payments"}, None)
    assert a != b


@pytest.mark.unit
def test_name_and_severity_affect_fingerprint() -> None:
    base = fingerprint(TENANT, "X", {"service": "checkout"}, "warning")
    assert base != fingerprint(TENANT, "Y", {"service": "checkout"}, "warning")
    assert base != fingerprint(TENANT, "X", {"service": "checkout"}, "critical")


@pytest.mark.unit
def test_tenant_scopes_fingerprint() -> None:
    a = fingerprint(new_id(), "X", {"service": "checkout"}, None)
    b = fingerprint(new_id(), "X", {"service": "checkout"}, None)
    assert a != b


@pytest.mark.unit
def test_custom_unstable_labels_override() -> None:
    # With a custom unstable set, 'zone' is dropped → equal fingerprints.
    a = fingerprint(TENANT, "X", {"service": "c", "zone": "a"}, None, unstable_labels=("zone",))
    b = fingerprint(TENANT, "X", {"service": "c", "zone": "b"}, None, unstable_labels=("zone",))
    assert a == b
