# Spec: Admin bootstrap for the first tenant API key

> `POST /v1/auth/api-keys` requires bearer auth with an existing API key, but a
> freshly created tenant has none and tenant creation doesn't return one — the
> first key can only be minted by reaching into the DB (ssh into prod). This
> spec lets the admin token issue a key for an explicit tenant, closing the
> chicken-and-egg.

**Spec ID:** 0018
**Status:** ready-for-agent
**Author:** Gurbaksh Singh Gabbi (+ Claude)
**Created:** 2026-08-18

---

## Motivation

FR-1.2 requires tenant onboarding (create tenant → API key → first service) in
under 15 minutes; `fly ssh console` + a Python snippet is not that.
`docs/05-api-spec.md` already labels `POST /v1/auth/api-keys` "(admin only)" —
the intent existed, spec 0001's implementation authenticated it with a tenant
key instead, and `tests/integration/test_auth_flow.py` works around the gap by
minting the first key through the service layer. This is the surfaced
docs-vs-code contradiction, resolved in the docs' favour (with the wording
fixed: an admin has no "calling tenant", so the admin path must name one).

---

## Scope

- [ ] `POST /v1/auth/api-keys` accepts **either** bearer auth mode:
      a tenant API key (existing behaviour, self-service rotation) **or** the
      admin shared secret plus an explicit `tenant_id` in the body.
- [ ] New dependency `current_tenant_or_admin` in `api/deps.py`.
- [ ] `ApiKeyCreateRequest` gains optional `tenant_id`.
- [ ] Admin path validates the tenant exists (404, not an FK 500).
- [ ] Tenant-key path rejects a mismatched `tenant_id` (403).
- [ ] Update `docs/05-api-spec.md` (both auth modes; fix "(admin only)" wording).
- [ ] Update `docs/06-deploy-fly.md` bootstrap section (curl replaces ssh).
- [ ] Tests for the above.

---

## Out of scope

- A real admin identity/login (the shared secret stays; see `admin_only`).
- Returning an initial key from `POST /v1/tenant` (rejected alternative: hides
  key issuance inside tenant creation and complicates idempotent retries).
- Per-key scopes/permissions.

---

## Context

- `docs/05-api-spec.md` — Auth section.
- `src/ai_sre/api/deps.py` — `current_tenant`, `admin_only`.
- `src/ai_sre/api/tenants.py` — `create_api_key`.
- `src/ai_sre/schemas/api_key.py`
- `src/ai_sre/core/tenant/api_key_service.py` — `issue()` does not validate
  tenant existence; the route must.
- `tests/integration/test_auth_flow.py` — the service-layer bootstrap
  workaround this spec deletes.

---

## Design

### Files to touch

- `src/ai_sre/api/deps.py` — add `current_tenant_or_admin`.
- `src/ai_sre/api/tenants.py` — rewire `create_api_key`.
- `src/ai_sre/schemas/api_key.py` — optional `tenant_id` on the request.
- `docs/05-api-spec.md`, `docs/06-deploy-fly.md` — behaviour docs.
- `tests/integration/test_auth_flow.py` — new cases; replace the workaround.

### New / changed contracts

```python
# api/deps.py
async def current_tenant_or_admin(
    authorization: str = Header(..., alias="Authorization"),
    api_key_service: ApiKeyService = Depends(get_api_key_service),
) -> TenantContext | None:
    """Admin shared secret → None; otherwise defer to current_tenant."""

# schemas/api_key.py
class ApiKeyCreateRequest(BaseModel):
    name: str
    tenant_id: UUID | None = None  # required for admin auth; else must match caller
```

Route logic (`create_api_key`):
- caller is admin (`None`): `tenant_id` missing → 400 `api_key.tenant_id_required`;
  tenant not found → 404 `tenant.not_found`; else issue.
- caller is a tenant: `tenant_id` set and ≠ caller → 403 `api_key.tenant_mismatch`;
  else issue for the caller.

### Data model changes

None.

### Edge cases

- Admin token compared with `secrets.compare_digest` (constant-time).
- A token that is neither the admin secret nor a valid API key → 401
  (unchanged `current_tenant` behaviour).
- Admin + `tenant_id` of a suspended tenant: allowed (key issuance is an
  admin act; the key still won't verify while the tenant isn't active —
  `ApiKeyService.verify` already enforces tenant status).

---

## Tests

- Integration (`test_auth_flow.py`): admin creates tenant → admin issues first
  key with `tenant_id` → key calls a protected route; admin without
  `tenant_id` → 400; admin with unknown `tenant_id` → 404; tenant key with
  foreign `tenant_id` → 403; existing self-service tests keep passing with the
  workaround removed.

---

## Rollout

- Migrations required? N
- Backward compatible? Y (existing callers unchanged)
- Feature flag? none
- Observability: `api_key.issued` already fires in the service; the route
  additionally logs `api_key.admin_issued` (tenant_id) on the admin path so
  bootstrap issuances are auditable.

---

## Definition of done

- [ ] All "Scope" items implemented.
- [ ] Tests written and passing.
- [ ] `make lint` clean.
- [ ] `make test` green.
- [ ] Docs updated (05-api-spec, 06-deploy-fly) and called out in the PR.
- [ ] No new dependencies.

---

## Follow-ups

- Real admin authn (replace the shared secret) once a dashboard exists.
