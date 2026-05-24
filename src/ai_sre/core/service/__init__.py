"""Subject-service domain.

A *subject service* is the focal point of every investigation — the customer
chooses one set of labels that identifies their service in Prometheus, and
the platform pivots its topology, metrics, and alerts around it.

The public entry point is :class:`ServiceService`; routes obtain it via
:func:`ai_sre.api.deps.get_service_service`. The repository is a
:class:`ai_sre.core._base.repository.TenantScopedRepository` so every query
carries the tenant filter automatically.
"""
