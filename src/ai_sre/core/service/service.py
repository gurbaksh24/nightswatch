"""ServiceService — register and read the tenant's subject service.

Composes the repository with the connector registry so registration can
validate the label selector against the tenant's connected Prometheus.

Responsibilities:
    * Persist the service via the repository, surfacing typed domain
      exceptions on conflicts.
    * Validate ``label_selector`` against Prometheus on registration. If
      no Prometheus integration is connected, register with a
      ``validation_pending`` flag stashed in ``slo_config``.
    * Return registration outcomes plus optional warnings (e.g. "selector
      matched zero series") so the API layer can surface them in the
      response.

See spec ``specs/0004-service-registration.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from ai_sre.connectors.base import (
    ConnectorKind,
    PromQLQuery,
    RawPromQL,
)
from ai_sre.connectors.registry import ConnectorRegistry
from ai_sre.core.service.repository import ServiceRepository
from ai_sre.exceptions import (
    ConnectorError,
    ConnectorTimeout,
    IntegrationError,
    IntegrationNotFound,
    IntegrationUnhealthy,
)
from ai_sre.models.service import Service
from ai_sre.schemas.service import LabelSelectorValidation
from ai_sre.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ServiceRegistrationResult:
    """Outcome of :meth:`ServiceService.register`.

    Carries the persisted row plus any non-fatal warnings (e.g. "selector
    matched zero series") that the API layer should surface to the caller.
    """

    service: Service
    warnings: list[str] = field(default_factory=list)


class ServiceService:
    """Register / read the tenant's subject service.

    The repository is already tenant-scoped; this service doesn't need to
    re-pass the tenant id — it's baked in.
    """

    def __init__(
        self,
        repo: ServiceRepository,
        connector_registry: ConnectorRegistry,
    ) -> None:
        self.repo = repo
        self.connector_registry = connector_registry

    @property
    def tenant_id(self) -> UUID:
        return self.repo.tenant_id

    # ---- Public API ----

    async def register(
        self,
        *,
        name: str,
        label_selector: dict[str, str],
        ownership: dict[str, Any] | None = None,
    ) -> ServiceRegistrationResult:
        """Persist the tenant's service after validating the selector.

        Behaviour:
            * No Prometheus integration → service is persisted with
              ``slo_config = {"validation_pending": True}`` and no warning
              (this is expected, not exceptional).
            * Prometheus integration exists but the validation query failed
              (auth/network/timeout) → service is persisted with
              ``validation_pending=True`` and a warning carrying the error.
            * Selector matched zero series → persisted normally with a
              warning. The tenant can later refresh.
            * Selector matched ≥1 series → persisted normally, no warnings.

        Raises:
            ServiceAlreadyExists: tenant already has a service (MVP rule).
        """
        validation = await self.validate_label_selector(label_selector)
        slo_config, warnings = self._build_slo_config_and_warnings(validation)

        row = await self.repo.create(
            name=name,
            label_selector=label_selector,
            ownership=ownership,
            slo_config=slo_config,
        )
        # Log only the label keys — values can be customer-specific and we
        # don't want them in our log pipeline by default (per spec 0004
        # rollout notes).
        logger.info(
            "service.registered",
            tenant_id=str(self.tenant_id),
            service_id=str(row.id),
            selector_keys=sorted(label_selector.keys()),
            validation_pending=bool(slo_config and slo_config.get("validation_pending")),
            series_count=validation.series_count,
        )
        return ServiceRegistrationResult(service=row, warnings=warnings)

    async def get(self, service_id: UUID) -> Service | None:
        """Return the service by id, or ``None`` if not owned by this tenant."""
        return await self.repo.get_by_id(service_id)

    async def get_current(self) -> Service | None:
        """Return the tenant's single service, if any.

        Useful for "is the tenant onboarded?" checks without needing the id.
        """
        return await self.repo.get_for_tenant()

    async def update(
        self,
        service_id: UUID,
        *,
        name: str | None = None,
        ownership: dict[str, Any] | None = None,
    ) -> Service | None:
        """Update mutable metadata on the service. ``label_selector`` is
        immutable in MVP — see spec 0004's "Out of scope" note.

        Returns the updated row, or ``None`` if the id isn't owned by this
        tenant.
        """
        return await self.repo.update_metadata(
            service_id, name=name, ownership=ownership
        )

    async def validate_label_selector(
        self, selector: dict[str, str]
    ) -> LabelSelectorValidation:
        """Probe the tenant's Prometheus for series matching ``selector``.

        Returns a structured outcome — never raises for "selector matched
        zero" or "Prometheus not configured"; those are normal-path
        results. Connection-level failures (auth, timeout) are captured in
        the ``error`` field so the caller can surface them.

        The selector is translated to ``up{label="value",...}``: every
        scraped Prometheus target emits ``up``, so this is a cheap way to
        ask "is there anything at all matching these labels?".
        """
        try:
            connector = await self.connector_registry.get(
                self.tenant_id, ConnectorKind.PROMETHEUS
            )
        except (IntegrationNotFound, IntegrationUnhealthy):
            return LabelSelectorValidation(has_prometheus=False, series_count=0)
        except IntegrationError as exc:
            logger.warning(
                "service.validate_selector.connector_lookup_failed",
                tenant_id=str(self.tenant_id),
                error=str(exc),
            )
            return LabelSelectorValidation(
                has_prometheus=False, series_count=0, error=str(exc)
            )

        promql = self._build_existence_query(selector)
        try:
            result = await connector.query(
                PromQLQuery(intent=RawPromQL(query=promql))
            )
        except ConnectorTimeout as exc:
            return LabelSelectorValidation(
                has_prometheus=True, series_count=0, error=f"timeout: {exc}"
            )
        except ConnectorError as exc:
            return LabelSelectorValidation(
                has_prometheus=True, series_count=0, error=str(exc)
            )

        if not result.success:
            return LabelSelectorValidation(
                has_prometheus=True,
                series_count=0,
                error=result.error or "Prometheus query failed",
            )
        return LabelSelectorValidation(
            has_prometheus=True,
            series_count=result.series_count,
        )

    # ---- internals ----

    @staticmethod
    def _escape_label_value(value: str) -> str:
        """Escape a label value for inclusion in a PromQL label matcher.

        Mirrors the escaping used by
        :func:`ai_sre.connectors.prometheus.queries._escape`. Kept inline so
        we don't depend on a sibling module's private helper.
        """
        return value.replace("\\", "\\\\").replace('"', '\\"')

    @classmethod
    def _build_existence_query(cls, selector: dict[str, str]) -> str:
        """Build ``up{label="value",...}`` for the validation probe."""
        if not selector:
            return "up"
        parts = [
            f'{k}="{cls._escape_label_value(v)}"'
            for k, v in sorted(selector.items())
        ]
        return "up{" + ",".join(parts) + "}"

    @staticmethod
    def _build_slo_config_and_warnings(
        validation: LabelSelectorValidation,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """Decide what to store + what to tell the user.

        Returns ``(slo_config, warnings)`` — ``slo_config`` carries
        ``{"validation_pending": True}`` when registration outpaced the
        Prometheus integration; warnings are user-facing strings.
        """
        warnings: list[str] = []
        slo_config: dict[str, Any] | None = None

        if not validation.has_prometheus:
            slo_config = {"validation_pending": True}
            if validation.error:
                # We tried to look up the connector and something went
                # wrong before we could even reach Prometheus.
                warnings.append(
                    "Could not look up Prometheus integration: "
                    f"{validation.error}"
                )
            return slo_config, warnings

        if validation.error:
            # Prometheus exists but the validation call failed (auth/network).
            slo_config = {"validation_pending": True}
            warnings.append(
                "Could not validate label_selector against Prometheus: "
                f"{validation.error}. The service was registered; re-run "
                "validation after fixing the integration."
            )
            return slo_config, warnings

        if validation.series_count == 0:
            warnings.append(
                "label_selector matched 0 series in Prometheus. The service "
                "was registered, but investigations won't be able to query "
                "metrics until at least one series matches."
            )

        return slo_config, warnings
