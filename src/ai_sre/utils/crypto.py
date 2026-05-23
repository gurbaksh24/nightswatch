"""Cryptographic helpers.

Three responsibilities:
    1. **Envelope encryption** for tenant secrets (integration credentials,
       webhook signing secrets). MVP uses a static 32-byte root key from
       settings; production swaps to a KMS-backed DEK by replacing the
       ``EnvelopeEncryptionService`` implementation — the interface is
       shaped to be KMS-compatible.
    2. **HMAC** for webhook signature verification (constant-time).
    3. **SHA-256 hashing** for API key storage.

Do not call ``cryptography`` primitives directly outside this module.

Encrypted-blob layout (versioned so we can rotate algorithms later):
    byte 0       version (currently 0x01 = AES-256-GCM)
    bytes 1..12  random 96-bit nonce
    bytes 13..N  ciphertext || 16-byte GCM tag (concatenated by AESGCM)

The blob is opaque to callers — store it in a ``BYTEA`` column and pass it
back to ``decrypt`` unchanged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Format version. Bump this if you change the algorithm or key derivation.
_BLOB_VERSION = 0x01
_NONCE_LEN = 12  # 96 bits — recommended for AES-GCM


class EnvelopeEncryptionError(Exception):
    """Raised when decryption fails — bad key, tampered ciphertext, or
    unknown blob version."""


class EnvelopeEncryptionService:
    """Symmetric authenticated encryption for tenant secrets.

    Shape matches what a KMS-backed implementation would expose, so the
    in-process MVP impl can be swapped out without changing callers.

    Args:
        master_key_b64: URL-safe base64-encoded 32-byte key (the same
            shape the existing Fernet key uses, for env-var continuity).
    """

    def __init__(self, master_key_b64: str) -> None:
        try:
            key = base64.urlsafe_b64decode(master_key_b64.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise EnvelopeEncryptionError(
                "master_key_b64 is not valid url-safe base64."
            ) from exc
        if len(key) != 32:
            raise EnvelopeEncryptionError(
                f"master key must decode to 32 bytes, got {len(key)}."
            )
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt ``plaintext`` to a versioned, authenticated blob.

        Produces a fresh random nonce for every call — never reuse.
        """
        nonce = secrets.token_bytes(_NONCE_LEN)
        ct_and_tag = self._aead.encrypt(nonce, plaintext, associated_data=None)
        return bytes([_BLOB_VERSION]) + nonce + ct_and_tag

    def decrypt(self, blob: bytes) -> bytes:
        """Decrypt a blob produced by :meth:`encrypt`.

        Raises:
            EnvelopeEncryptionError: on unknown version, truncated input,
                wrong key, or tampered ciphertext.
        """
        if len(blob) < 1 + _NONCE_LEN + 16:
            raise EnvelopeEncryptionError("ciphertext too short to be valid.")
        version = blob[0]
        if version != _BLOB_VERSION:
            raise EnvelopeEncryptionError(
                f"unknown blob version {version:#04x}; expected {_BLOB_VERSION:#04x}."
            )
        nonce = blob[1 : 1 + _NONCE_LEN]
        ct_and_tag = blob[1 + _NONCE_LEN :]
        try:
            return self._aead.decrypt(nonce, ct_and_tag, associated_data=None)
        except InvalidTag as exc:
            raise EnvelopeEncryptionError(
                "decryption failed — wrong key or tampered ciphertext."
            ) from exc


# ---- Legacy / utility helpers (unchanged from spec 0001) ----


def _fernet(key_b64: str) -> Fernet:
    return Fernet(key_b64.encode("ascii"))


def encrypt_tenant_secret(plaintext: bytes, root_key_b64: str) -> bytes:
    """Legacy Fernet-based helper. Prefer ``EnvelopeEncryptionService``.

    Kept for callers that haven't migrated; not used by the integration
    code path.
    """
    return _fernet(root_key_b64).encrypt(plaintext)


def decrypt_tenant_secret(ciphertext: bytes, root_key_b64: str) -> bytes:
    """Legacy Fernet-based helper. See ``encrypt_tenant_secret``."""
    return _fernet(root_key_b64).decrypt(ciphertext)


def hash_api_key(raw_key: str) -> str:
    """SHA-256 hex digest of an API key for storage."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def verify_hmac_signature(
    *, secret: bytes, body: bytes, signature_header: str
) -> bool:
    """Verify ``sha256=<hex>`` HMAC over the raw body. Constant-time."""
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    actual = signature_header[len("sha256=") :]
    return hmac.compare_digest(expected, actual)


def random_base64_key(num_bytes: int = 32) -> str:
    """Generate a base64-encoded random key (32 bytes by default)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(num_bytes)).decode("ascii")
