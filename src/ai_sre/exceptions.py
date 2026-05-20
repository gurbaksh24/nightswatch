"""Typed exception hierarchy.

All custom exceptions in `core/`, `connectors/`, `llm/`, and `delivery/`
inherit from `AISREError`. The `api/` layer translates these to HTTP responses.
"""

from __future__ import annotations


class AISREError(Exception):
    """Base for all platform exceptions."""

    code: str = "internal"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


# ---- Auth ----
class AuthError(AISREError):
    code = "auth.error"


class InvalidApiKey(AuthError):
    code = "auth.invalid_token"


class InvalidWebhookSignature(AuthError):
    code = "auth.invalid_signature"


# ---- Tenant ----
class TenantError(AISREError):
    code = "tenant.error"


class TenantNotFound(TenantError):
    code = "tenant.not_found"


class TenantSlugTaken(TenantError):
    code = "tenant.slug_taken"


# ---- Integration ----
class IntegrationError(AISREError):
    code = "integration.error"


class IntegrationUnhealthy(IntegrationError):
    code = "integration.unhealthy"


# ---- Service ----
class ServiceAlreadyExists(AISREError):
    """Tenant tried to register a second service in MVP."""

    code = "service.already_exists"


# ---- Investigation ----
class InvestigationError(AISREError):
    code = "investigation.error"


class InvestigationNotFound(InvestigationError):
    code = "investigation.not_found"


class StageError(InvestigationError):
    code = "investigation.stage_error"


# ---- Budget ----
class BudgetExhausted(AISREError):
    code = "budget.exhausted"


# ---- Connector ----
class ConnectorError(AISREError):
    code = "connector.error"


class ConnectorTimeout(ConnectorError):
    code = "connector.timeout"


class ConnectorUnsupported(ConnectorError):
    """Raised when a connector is asked for a query type it doesn't support."""

    code = "connector.unsupported"


# ---- LLM ----
class LLMError(AISREError):
    code = "llm.error"


class LLMResponseInvalid(LLMError):
    code = "llm.invalid_response"


# ---- Delivery ----
class DeliveryError(AISREError):
    code = "delivery.error"
