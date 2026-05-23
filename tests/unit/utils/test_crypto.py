"""Unit tests for :class:`EnvelopeEncryptionService`.

Cover the contract:
    * Round-trip: ``decrypt(encrypt(plaintext)) == plaintext``.
    * Each call produces a unique ciphertext (random nonce).
    * Tampering anywhere in the blob fails authentication.
    * Truncated blobs are rejected cleanly.
    * The version byte is enforced.
    * Bad master key sizes / encodings raise on construction.
"""

from __future__ import annotations

import pytest

from ai_sre.utils.crypto import (
    EnvelopeEncryptionError,
    EnvelopeEncryptionService,
    random_base64_key,
)


@pytest.fixture
def crypto() -> EnvelopeEncryptionService:
    return EnvelopeEncryptionService(random_base64_key())


@pytest.mark.unit
def test_roundtrip(crypto: EnvelopeEncryptionService) -> None:
    plaintext = b'{"url": "https://prom.example.com", "auth": {"type": "bearer", "token": "s3cr3t"}}'
    blob = crypto.encrypt(plaintext)
    assert crypto.decrypt(blob) == plaintext


@pytest.mark.unit
def test_each_encryption_is_unique(crypto: EnvelopeEncryptionService) -> None:
    """Random nonce ensures the same plaintext yields different ciphertexts."""
    pt = b"identical"
    a = crypto.encrypt(pt)
    b = crypto.encrypt(pt)
    assert a != b
    assert crypto.decrypt(a) == pt
    assert crypto.decrypt(b) == pt


@pytest.mark.unit
def test_blob_layout(crypto: EnvelopeEncryptionService) -> None:
    """1 byte version || 12 byte nonce || ciphertext+tag."""
    blob = crypto.encrypt(b"x" * 32)
    assert blob[0] == 0x01  # current version
    # plaintext (32) + GCM tag (16) = 48; plus header (13) = 61
    assert len(blob) == 1 + 12 + 32 + 16


@pytest.mark.unit
def test_tampering_is_detected(crypto: EnvelopeEncryptionService) -> None:
    blob = bytearray(crypto.encrypt(b"sensitive"))
    # Flip a single bit somewhere in the ciphertext portion (after header).
    blob[20] ^= 0x01
    with pytest.raises(EnvelopeEncryptionError):
        crypto.decrypt(bytes(blob))


@pytest.mark.unit
def test_truncated_blob_is_rejected(crypto: EnvelopeEncryptionService) -> None:
    blob = crypto.encrypt(b"x")
    with pytest.raises(EnvelopeEncryptionError, match="too short"):
        crypto.decrypt(blob[:10])


@pytest.mark.unit
def test_unknown_version_is_rejected(crypto: EnvelopeEncryptionService) -> None:
    blob = bytearray(crypto.encrypt(b"x"))
    blob[0] = 0xFF
    with pytest.raises(EnvelopeEncryptionError, match="unknown blob version"):
        crypto.decrypt(bytes(blob))


@pytest.mark.unit
def test_different_keys_cannot_decrypt() -> None:
    a = EnvelopeEncryptionService(random_base64_key())
    b = EnvelopeEncryptionService(random_base64_key())
    blob = a.encrypt(b"hello")
    with pytest.raises(EnvelopeEncryptionError):
        b.decrypt(blob)


@pytest.mark.unit
def test_bad_master_key_rejects_on_init() -> None:
    with pytest.raises(EnvelopeEncryptionError, match="not valid url-safe base64"):
        EnvelopeEncryptionService("this is not base64 !@#")


@pytest.mark.unit
def test_wrong_size_master_key_rejects() -> None:
    import base64

    too_short = base64.urlsafe_b64encode(b"x" * 16).decode("ascii")
    with pytest.raises(EnvelopeEncryptionError, match="32 bytes"):
        EnvelopeEncryptionService(too_short)
