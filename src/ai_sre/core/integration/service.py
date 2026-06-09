"""IntegrationService — orchestrates the repository and envelope encryption.

Responsibilities:
    * Split incoming config into a *public* dict (safe to show in UI/API
      responses) and a *secret* blob (envelope-encrypted, persisted as
      ``BYTEA``).
    * Persist the integration via the repository, surfacing typed
      domain exceptions on conflicts.
    * Expose ``decrypt_config`` so later specs (the Prometheus connector
      in 0003, Slack delivery in 0010) can pull plaintext credentials when
      they actually need to talk to the external system.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from ai_sre.core.integration.repository import IntegrationRepository
from ai_sre.exceptions import (
    IntegrationCredentialDecryptionFailed,
    IntegrationNotFound,
)
from ai_sre.models.integration import Integration
from ai_sre.utils.crypto import EnvelopeEncryptionError, EnvelopeEncryptionService
from ai_sre.utils.logging import get_logger
from ai_sre.utils.webhook_signature import generate_webhook_secret

logger = get_logger(__name__)


class IntegrationService:
    """Create, read, list, and delete integrations for a tenant.

    The repository is already tenant-scoped; the service layer doesn't need
    to re-pass the tenant id — it's baked in.
    """

    def __init__(
        self,
        repo: IntegrationRepository,
        crypto: EnvelopeEncryptionService,
    ) -> None:
        self.repo = repo
        self.crypto = crypto

    @property
    def tenant_id(self) -> UUID:
        return self.repo.tenant_id

    async def create(
        self, *, kind: str, name: str, config: dict[str, Any]
    ) -> Integration:
        """Persist a new integration with its config envelope-encrypted.

        Raises:
            IntegrationAlreadyExists: on duplicate ``(kind, name)``.
        """
        encrypted, public = self._split_config(kind, config)
        row = await self.repo.create(
            kind=kind,
            name=name,
            config_encrypted=encrypted,
            config_public=public,
        )
        logger.info(
            "integration.created",
            tenant_id=str(self.tenant_id),
            integration_id=str(row.id),
            kind=kind,
            name=name,
        )
        return row

    async def get(self, integration_id: UUID) -> Integration | None:
        """Return the integration by id, or ``None``."""
        return await self.repo.get_by_id(integration_id)

    async def list(self) -> Sequence[Integration]:
        """List all integrations for the current tenant."""
        return await self.repo.list_all()

    async def delete(self, integration_id: UUID) -> None:
        """Delete an integration. Raises :class:`IntegrationNotFound` if
        the id isn't owned by this tenant (or doesn't exist).
        """
        removed = await self.repo.delete(integration_id)
        if not removed:
            raise IntegrationNotFound(
                f"Integration {integration_id} not found.",
                details={"integration_id": str(integration_id)},
            )
        logger.info(
            "integration.deleted",
            tenant_id=str(self.tenant_id),
            integration_id=str(integration_id),
        )

    async def generate_webhook_secret(self, integration_id: UUID) -> str:
        """Generate, store (encrypted), and return a webhook signing secret.

        Used at Prometheus integration creation and for rotation. The
        plaintext is returned to the caller once and never persisted in the
        clear. Raises :class:`IntegrationNotFound` if the id isn't owned by
        this tenant.
        """
        secret = generate_webhook_secret()
        encrypted = self.crypto.encrypt(secret.encode("utf-8"))
        row = await self.repo.set_webhook_secret(integration_id, encrypted)
        if row is None:
            raise IntegrationNotFound(
                f"Integration {integration_id} not found.",
                details={"integration_id": str(integration_id)},
            )
        logger.info(
            "integration.webhook_secret_generated",
            tenant_id=str(self.tenant_id),
            integration_id=str(integration_id),
        )
        return secret

    def get_webhook_secret(self, integration: Integration) -> str | None:
        """Return the plaintext webhook signing secret, or ``None`` if the
        integration has none set.

        Raises :class:`IntegrationCredentialDecryptionFailed` on a corrupt
        blob / wrong key.
        """
        blob = integration.webhook_signing_secret_encrypted
        if blob is None:
            return None
        try:
            return self.crypto.decrypt(blob).decode("utf-8")
        except EnvelopeEncryptionError as exc:
            logger.error(
                "integration.webhook_secret_decrypt_failed",
                tenant_id=str(self.tenant_id),
                integration_id=str(integration.id),
                error=str(exc),
            )
            raise IntegrationCredentialDecryptionFailed(
                f"Could not decrypt webhook secret for integration {integration.id}.",
                details={"integration_id": str(integration.id)},
            ) from exc

    def decrypt_config(self, integration: Integration) -> dict[str, Any]:
        """Return the plaintext config dict for an integration.

        Used by the Prometheus connector (spec 0003), Slack delivery
        (spec 0010), etc. Raises
        :class:`IntegrationCredentialDecryptionFailed` if the blob is
        corrupt or the encryption key has been rotated incorrectly.
        """
        try:
            plaintext = self.crypto.decrypt(integration.config_encrypted)
        except EnvelopeEncryptionError as exc:
            logger.error(
                "integration.decrypt_failed",
                tenant_id=str(self.tenant_id),
                integration_id=str(integration.id),
                error=str(exc),
            )
            raise IntegrationCredentialDecryptionFailed(
                f"Could not decrypt config for integration {integration.id}.",
                details={"integration_id": str(integration.id)},
            ) from exc
        return json.loads(plaintext.decode("utf-8"))  # type: ignore[no-any-return]

    # ---- internals ----

    def _split_config(
        self, kind: str, config: dict[str, Any]
    ) -> tuple[bytes, dict[str, Any]]:
        """Split incoming config into ``(encrypted_blob, public_dict)``.

        Per :doc:`docs/05-api-spec.md`, ``config_public`` is what's safe to
        show in the UI/API responses (host, auth type). The encrypted blob
        holds the full thing so connectors can reconstruct it later.
        """
        encrypted = self.crypto.encrypt(json.dumps(config).encode("utf-8"))
        public = self._public_view(kind, config)
        return encrypted, public

    @staticmethod
    def _public_view(kind: str, config: dict[str, Any]) -> dict[str, Any]:
        """Return the non-secret subset of ``config`` for display.

        Currently only ``prometheus`` is supported; future kinds add
        branches here.
        """
        if kind == "prometheus":
            parsed = urlparse(str(config.get("url", "")))
            host = parsed.hostname or ""
            auth_type = "none"
            auth = config.get("auth")
            if isinstance(auth, dict) and isinstance(auth.get("type"), str):
                auth_type = auth["type"]
            return {"url_host": host, "auth_type": auth_type}
        # Defensive: unknown kinds get an empty public view rather than
        # leaking arbitrary config. The API layer should never allow this
        # branch to fire (Literal["prometheus"] guard), but the service
        # is the security boundary.
        return {}
