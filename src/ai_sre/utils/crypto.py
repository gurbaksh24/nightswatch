"""Cryptographic helpers.

Two responsibilities:
    1. **Envelope encryption** for tenant secrets (integration credentials,
       webhook signing secrets). MVP uses a single root key from settings;
       production MUST replace with KMS-backed DEKs.
    2. **HMAC** for webhook signature verification (constant-time).

Do not call `cryptography` primitives directly outside this module.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet


def _fernet(key_b64: str) -> Fernet:
    return Fernet(key_b64.encode("ascii"))


def encrypt_tenant_secret(plaintext: bytes, root_key_b64: str) -> bytes:
    """Encrypt arbitrary bytes with the tenant root key.

    Production: replace with envelope encryption (KMS-managed DEK per tenant).
    """
    return _fernet(root_key_b64).encrypt(plaintext)


def decrypt_tenant_secret(ciphertext: bytes, root_key_b64: str) -> bytes:
    return _fernet(root_key_b64).decrypt(ciphertext)


def hash_api_key(raw_key: str) -> str:
    """SHA-256 hex digest of an API key for storage."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def verify_hmac_signature(
    *, secret: bytes, body: bytes, signature_header: str
) -> bool:
    """Verify `sha256=<hex>` HMAC over the raw body. Constant-time."""
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    actual = signature_header[len("sha256=") :]
    return hmac.compare_digest(expected, actual)


def random_base64_key(num_bytes: int = 32) -> str:
    """Generate a base64-encoded random key suitable for Fernet (32 bytes)."""
    import secrets

    return base64.urlsafe_b64encode(secrets.token_bytes(num_bytes)).decode("ascii")
