"""Integration domain.

The ``IntegrationService`` is the only public entry point; routes get it via
:func:`ai_sre.api.deps.get_integration_service`. The repository is a
``TenantScopedRepository`` and never bypasses the tenant filter.
"""
