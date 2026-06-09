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

import secrets

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
    import hashlib
    import hmac

    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"{_SCHEME}{digest}"


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
