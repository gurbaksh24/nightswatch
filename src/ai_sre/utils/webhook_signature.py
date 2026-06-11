"""Inbound webhook signing — generate, sign, verify.

Alertmanager (and other webhook sources) authenticate to us with a per-tenant
shared secret: they send ``X-AI-SRE-Signature: sha256=<hex>`` where ``hex`` is
``HMAC-SHA256(secret, raw_body)``. We verify with a constant-time compare.

The secret is generated here, stored envelope-encrypted on the integration
(``webhook_signing_secret_encrypted``), and shown to the tenant once so they
can configure their Alertmanager webhook.

See docs/05-api-spec.md "Webhook payload — signature scheme" and spec 0006.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from ai_sre.utils.crypto import verify_hmac_signature

_SCHEME = "sha256="


def generate_webhook_secret(num_bytes: int = 32) -> str:
    """Generate a new URL-safe webhook signing secret (shown once)."""
    return secrets.token_urlsafe(num_bytes)


def sign(secret: str, body: bytes) -> str:
    """Return the ``sha256=<hex>`` signature header value for ``body``.

    Used by tests and for documenting how Alertmanager should sign; the
    server only ever *verifies*.
    """
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"{_SCHEME}{digest}"


def slack_signature(signing_secret: str, timestamp: str, body: bytes) -> str:
    """Compute Slack's ``v0=<hex>`` request signature (used by tests)."""
    basestring = b"v0:" + timestamp.encode("utf-8") + b":" + body
    digest = hmac.new(
        signing_secret.encode("utf-8"), basestring, hashlib.sha256
    ).hexdigest()
    return f"v0={digest}"


def verify_slack_request(
    signing_secret: str,
    timestamp: str | None,
    body: bytes,
    signature: str | None,
    *,
    max_age_seconds: int = 300,
) -> bool:
    """Verify a Slack interactive request (``X-Slack-Signature`` /
    ``X-Slack-Request-Timestamp``). Rejects stale (>5 min) or mismatched
    requests. Constant-time compare.
    """
    if not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts) > max_age_seconds:
        return False
    expected = slack_signature(signing_secret, timestamp, body)
    return hmac.compare_digest(expected, signature)


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Constant-time verify a ``sha256=<hex>`` header against ``body``.

    Returns ``False`` for a missing/empty/malformed header rather than
    raising, so the caller maps any failure to a single 401.
    """
    if not signature_header:
        return False
    return verify_hmac_signature(
        secret=secret.encode("utf-8"),
        body=body,
        signature_header=signature_header,
    )
