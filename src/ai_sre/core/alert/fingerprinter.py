"""Deterministic alert fingerprinting.

The fingerprint is the dedupe key: two firings of the same alert on different
pods of the same service should collapse to one incident. We achieve that by
hashing the *stable* identity of an alert — its name, severity, and labels
*excluding* high-churn dimensions (pod, instance, …).

The function is pure and stable: the same inputs MUST produce the same digest
forever, because it's persisted on `alert.fingerprint` / `investigation.
fingerprint` and compared across time. Changing the canonical form is a
breaking change to dedupe and needs a migration story.

See LLD §6 and spec 0006.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from uuid import UUID

# Labels that vary per replica/scrape target and would otherwise split one
# incident into many fingerprints. Override per-tenant is a documented
# follow-up (spec 0006); for now this static default + an env override
# (see ``AppSettings``) is enough.
DEFAULT_UNSTABLE_LABELS: tuple[str, ...] = (
    "instance",
    "pod",
    "node",
    "container_id",
    "replica",
    "host",
)


def fingerprint(
    tenant_id: UUID,
    alert_name: str,
    labels: dict[str, str],
    severity: str | None,
    unstable_labels: Iterable[str] = DEFAULT_UNSTABLE_LABELS,
) -> str:
    """Return a deterministic SHA-256 hex digest identifying an alert.

    Args:
        tenant_id: scopes the fingerprint so two tenants never collide.
        alert_name: the Alertmanager ``alertname``.
        labels: the alert's labels; ``unstable_labels`` are dropped first.
        severity: the alert severity (``None`` treated as empty).
        unstable_labels: label keys excluded from the identity.

    The same inputs always yield the same output — see module docstring.
    """
    unstable = set(unstable_labels)
    stable = {k: v for k, v in labels.items() if k not in unstable}
    canonical = json.dumps(
        {
            "tenant": str(tenant_id),
            "name": alert_name,
            "labels": dict(sorted(stable.items())),
            "severity": severity or "",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
