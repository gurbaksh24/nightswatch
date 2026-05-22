# Spec 0004: Subject service registration

> One subject service per tenant in MVP. This spec registers the service, validates its label selector against the connected Prometheus, and exposes the read endpoints.

**Spec ID:** 0004
**Status:** ready-for-agent
**Depends on:** 0001, 0002, 0003

---

## Motivation

FR-1.3, FR-1.4: each tenant registers one service identified by label selectors. The service is the focal point of every investigation.

## Scope

- [ ] `Service` ORM model already exists; wire it through a repository + service.
- [ ] `ServiceService` (yes, awkward — open to renaming to `SubjectServiceService` or `ServiceRegistry`) with `register`, `get`, `update`.
- [ ] Enforce one-service-per-tenant via DB unique constraint on `(tenant_id)` (already present in the model) and a typed error.
- [ ] Validate `label_selector` against the connected Prometheus integration: at least one series must exist matching the selector. If no Prometheus integration is connected yet, allow registration but mark service as `validation_pending`.
- [ ] Implement `POST /v1/services`, `GET /v1/services/{id}`.
- [ ] Tests.

## Out of scope

- Topology + metric catalog discovery (spec 0005).
- Editing of label_selector after registration (treat as immutable in MVP; if it changes, delete + re-register).
- The `/topology` and `/metrics` sub-routes (those land in 0005).

## Context

- `docs/01-requirements.md` §3.1, §3.3
- `docs/03-lld.md` §3
- `docs/04-data-model.md` — `service`
- `src/ai_sre/models/service.py`
- `src/ai_sre/schemas/service.py`

## Design

### Files to touch

- `src/ai_sre/core/service/__init__.py` — new package.
- `src/ai_sre/core/service/service.py` — new (the service class).
- `src/ai_sre/core/service/repository.py` — new.
- `src/ai_sre/schemas/service.py` — implement (currently stub).
- `src/ai_sre/api/services.py` — implement.
- `migrations/versions/0004_service.py` — new.
- `tests/unit/core/test_service_service.py` — new.
- `tests/integration/test_service_routes.py` — new.

### New / changed contracts

```python
# core/service/service.py
class ServiceService:
    def __init__(
        self,
        repo: ServiceRepository,
        connector_registry: ConnectorRegistry,
    ) -> None: ...

    async def register(
        self, tenant_id: UUID, *, name: str, label_selector: dict[str, str], ownership: dict | None
    ) -> Service: ...

    async def get(self, tenant_id: UUID, service_id: UUID) -> Service | None: ...

    async def validate_label_selector(
        self, tenant_id: UUID, selector: dict[str, str]
    ) -> LabelSelectorValidation:
        """Returns presence + sample metric count. Used at registration and on demand."""
```

### Edge cases

- Tenant already has a service → raise `ServiceAlreadyExists` (already in `exceptions.py`); API returns 409.
- No Prometheus integration connected → register service with `validation_pending` flag in `slo_config` JSON (avoid a new column for one bit); skip the connector call.
- Label selector matches zero series → register but log warning; return service with a `warnings: [...]` field in the response.

## Tests

- Unit: register success; duplicate; validation against fake connector returning 0 vs N series.
- Integration: POST → GET roundtrip; tenant isolation; 409 on second registration.

## Rollout

- Migration: required if model needs adjustment (most fields already there).
- Observability: log `service.registered` with selector keys (not values, to avoid leaking customer-specific labels).

## Definition of done

- [ ] Scope complete.
- [ ] Tests passing.
- [ ] After running this spec on top of 0001–0003, a curl flow can: create tenant → create prometheus integration → register service → GET it.

## Follow-ups

- Service update / re-registration flow.
- Multi-service per tenant (deferred per requirements §5).
