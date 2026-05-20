# Spec 0001: Implement tenant creation and API key issuance

> Bootstraps the auth spine. After this lands, an admin can create a tenant, issue an API key, and every subsequent request can identify its tenant.

**Spec ID:** 0001
**Status:** ready-for-agent
**Author:** <name>
**Created:** 2026-05-13

---

## Motivation

Per FR-1.1 and FR-1.2, the system must support multiple tenants with isolated data and a tenant onboarding flow. Everything else in the platform depends on tenant identification, so this is the first thing to build.

---

## Scope

- [ ] `Tenant` ORM model + Alembic migration.
- [ ] `ApiKey` ORM model + migration.
- [ ] Pydantic schemas for tenant & API-key create/response.
- [ ] `TenantService` in `core/tenant/service.py` with `create`, `get_by_slug`, `update`.
- [ ] `ApiKeyService` with `create`, `verify`, `revoke`.
- [ ] `current_tenant` FastAPI dependency that parses Bearer token and resolves the tenant.
- [ ] Routes per `docs/05-api-spec.md`: `POST /v1/tenant` (admin), `GET /v1/tenant`, `PATCH /v1/tenant`, `POST /v1/auth/api-keys`, `GET /v1/auth/api-keys`, `DELETE /v1/auth/api-keys/{id}`.
- [ ] An admin-token mechanism for the first `POST /v1/tenant` call (env-configured shared secret for MVP).
- [ ] Unit tests for both services.
- [ ] Integration test: full create-tenant → issue-key → call protected route round trip.

---

## Out of scope

- SSO / SAML (deferred per requirements §5).
- Granular RBAC (single admin role per tenant in MVP).
- Per-tenant rate limiting.
- Tenant deletion (separate spec).

---

## Context

- `docs/01-requirements.md` §3.1, §4.5
- `docs/03-lld.md` §3, §4
- `docs/04-data-model.md` — `tenant`, `api_key` tables
- `docs/05-api-spec.md` — Auth and Tenants sections
- `src/ai_sre/db.py`
- `src/ai_sre/config.py`
- `src/ai_sre/api/deps.py`

---

## Design

### Files to touch

- `src/ai_sre/models/tenant.py` — new
- `src/ai_sre/models/api_key.py` — new
- `src/ai_sre/schemas/tenant.py` — new
- `src/ai_sre/schemas/api_key.py` — new
- `src/ai_sre/core/tenant/service.py` — implement (currently stub)
- `src/ai_sre/core/tenant/repository.py` — new
- `src/ai_sre/core/tenant/api_key_service.py` — new
- `src/ai_sre/api/tenants.py` — implement (currently stub)
- `src/ai_sre/api/deps.py` — implement `current_tenant`
- `migrations/versions/0001_tenants_and_api_keys.py` — new
- `tests/unit/core/test_tenant_service.py` — new
- `tests/unit/core/test_api_key_service.py` — new
- `tests/integration/test_auth_flow.py` — new

### New / changed contracts

```python
# core/tenant/service.py
class TenantService:
    def __init__(self, repo: TenantRepository) -> None: ...

    async def create(self, *, name: str, slug: str) -> Tenant: ...
    async def get(self, tenant_id: TenantId) -> Tenant | None: ...
    async def get_by_slug(self, slug: str) -> Tenant | None: ...
    async def update(self, tenant_id: TenantId, *, name: str | None = None) -> Tenant: ...

# core/tenant/api_key_service.py
@dataclass
class IssuedApiKey:
    id: UUID
    key: str        # plaintext, shown once
    prefix: str
    created_at: datetime

class ApiKeyService:
    async def issue(self, tenant_id: TenantId, *, name: str) -> IssuedApiKey: ...
    async def verify(self, raw_key: str) -> TenantContext | None: ...
    async def list(self, tenant_id: TenantId) -> list[ApiKey]: ...
    async def revoke(self, tenant_id: TenantId, key_id: UUID) -> None: ...
```

Key format: `ai-sre-live-<24 url-safe random chars>`. Stored as `sha256(key)`. Prefix = first 12 chars.

### Edge cases

- Slug uniqueness: enforce at DB layer (`UNIQUE`) and raise a typed `TenantSlugTaken` error from the service.
- Expired keys: `verify` returns `None`, the dependency raises `HTTPException(401)`.
- Revoked keys: same as expired.
- Constant-time comparison in `verify` (we lookup by `key_hash`, so it's already not vulnerable, but document it).

---

## Tests

**Unit (`tests/unit/core/`):**
- `test_tenant_service.py` — create, duplicate slug, get_by_slug.
- `test_api_key_service.py` — issue (key returned once, hash stored), verify happy + revoked + expired, list.

**Integration (`tests/integration/test_auth_flow.py`):**
- Admin endpoint with admin token creates tenant.
- That admin (or tenant-scoped) issues an API key.
- A GET to `/v1/tenant` with that key succeeds.
- Same GET with a revoked key returns 401.

---

## Rollout

- Migration required: Y (creates `tenant`, `api_key` tables).
- Backward compatible: Y (new tables only).
- Feature flag: none.
- Observability: log `tenant.created` and `api_key.issued` at INFO.

---

## Definition of done

- [ ] All scope items implemented.
- [ ] All listed tests written and passing.
- [ ] `make lint` clean.
- [ ] Migration runs on fresh DB.
- [ ] `curl` against a running dev server can: create tenant, issue key, call `/v1/tenant`.

---

## Follow-ups

- API key rotation flow (deferred).
- Audit log table for admin actions (deferred — single ADR-worthy decision).
- Magic-link / email login for human users (deferred to dashboard work).
