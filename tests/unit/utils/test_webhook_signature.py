"""Unit tests for webhook signature verification (spec 0006, 0013)."""

from __future__ import annotations

import time

import pytest

from ai_sre.utils.webhook_signature import (
    generate_webhook_secret,
    sign,
    slack_signature,
    verify_signature,
    verify_slack_request,
)


@pytest.mark.unit
def test_sign_then_verify_roundtrips() -> None:
    secret = generate_webhook_secret()
    body = b'{"alerts":[]}'
    header = sign(secret, body)
    assert header.startswith("sha256=")
    assert verify_signature(secret, body, header) is True


@pytest.mark.unit
def test_wrong_secret_fails() -> None:
    body = b"payload"
    header = sign("secret-a", body)
    assert verify_signature("secret-b", body, header) is False


@pytest.mark.unit
def test_tampered_body_fails() -> None:
    secret = generate_webhook_secret()
    header = sign(secret, b"original")
    assert verify_signature(secret, b"tampered", header) is False


@pytest.mark.unit
@pytest.mark.parametrize("header", [None, "", "deadbeef", "md5=abc", "sha256="])
def test_missing_or_malformed_header_fails(header: str | None) -> None:
    assert verify_signature("secret", b"body", header) is False


@pytest.mark.unit
def test_generated_secrets_are_unique() -> None:
    assert generate_webhook_secret() != generate_webhook_secret()


# ---- Slack request signing (spec 0013) ----

_SECRET = "8f742231b10e8888abcd99yyyzzz85a5"


@pytest.mark.unit
def test_slack_valid_signature_passes() -> None:
    ts = str(int(time.time()))
    body = b"payload=%7B%22type%22%3A%22block_actions%22%7D"
    sig = slack_signature(_SECRET, ts, body)
    assert verify_slack_request(_SECRET, ts, body, sig) is True


@pytest.mark.unit
def test_slack_wrong_signature_fails() -> None:
    ts = str(int(time.time()))
    body = b"payload=x"
    assert verify_slack_request(_SECRET, ts, body, "v0=deadbeef") is False


@pytest.mark.unit
def test_slack_wrong_secret_fails() -> None:
    ts = str(int(time.time()))
    body = b"payload=x"
    sig = slack_signature("other-secret", ts, body)
    assert verify_slack_request(_SECRET, ts, body, sig) is False


@pytest.mark.unit
def test_slack_stale_timestamp_rejected() -> None:
    ts = str(int(time.time()) - 600)  # 10 minutes old
    body = b"payload=x"
    sig = slack_signature(_SECRET, ts, body)  # signature itself is valid
    assert verify_slack_request(_SECRET, ts, body, sig) is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ts", "sig"),
    [(None, "v0=abc"), ("123", None), ("not-an-int", "v0=abc")],
)
def test_slack_missing_or_bad_headers_fail(ts: str | None, sig: str | None) -> None:
    assert verify_slack_request(_SECRET, ts, b"body", sig) is False
