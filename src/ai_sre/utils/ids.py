"""ID generation helpers.

UUIDv7 is preferred for all new primary keys: time-ordered, monotonic, and
indexes well. API keys are URL-safe random strings prefixed with their
environment marker.
"""

from __future__ import annotations

import secrets
from uuid import UUID

from ulid import ULID


def new_id() -> UUID:
    """Generate a UUIDv7-ish ID via ULID.

    ULIDs are 128-bit, time-ordered, and convertible to UUID. They give us
    the same on-the-wire shape as UUIDs while keeping inserts cluster-friendly.
    """
    return UUID(bytes=ULID().bytes)


_API_KEY_PREFIX_LEN = 8  # per docs/04-data-model.md §api_key and docs/05-api-spec.md example
_API_KEY_RAND_LEN = 24  # bytes -> ~32 chars urlsafe-base64


def generate_api_key(env: str = "live") -> tuple[str, str]:
    """Generate an API key. Returns `(full_key, prefix_for_display)`.

    Format: `ai-sre-<env>-<urlsafe>`.
    The full key MUST only ever be returned to the user once.
    Store sha256(full_key) for verification.
    """
    rand = secrets.token_urlsafe(_API_KEY_RAND_LEN)
    full = f"ai-sre-{env}-{rand}"
    prefix = full[:_API_KEY_PREFIX_LEN]
    return full, prefix
