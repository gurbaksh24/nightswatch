"""Unit tests for webhook signature verification (spec 0006)."""

from __future__ import annotations

import pytest

from ai_sre.utils.webhook_signature import (
    generate_webhook_secret,
    sign,
    verify_signature,
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
