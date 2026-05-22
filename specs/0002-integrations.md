# Spec 0002: Integration CRUD with envelope encryption

> Tenants connect external systems (Prometheus first, Slack later) via Integration records. Credentials are envelope-encrypted at rest. This spec implements the CRUD surface and the encryption primitive; connector-specific behaviour ships in later specs.

**Spec ID:** 0002
**Status:** ready-for-agent
**Depends on:** 0001

---

## Motivation

Per FR-2.1–2.4 and NFR-5.2, integrations are how tenants connect their stack. Everything downstream (Prometheus queries, Slack delivery) needs an Integration to exist first. This is the second piece of the spine.

## Scope

- [ ] `Integration` ORM model (already stubbed in `models/integration.py`) wired with kind/name/encrypted-config/public-config/status/etc.
- [ ] `EnvelopeEncryptionService` in `infrastructure/crypto/` (or `utils/crypto.py` if the package doesn't exist yet) that encrypts JSON with a KMS-managed key. For MVP, supports a static 32-byte key from settings; the API is the same shape KMS would have.
- [ ] `IntegrationService` and `IntegrationRepository` in `core/integration/`.
- [ ] Pydantic schemas in `schemas/integration.py` (already stubbed) for create/read/update.
- [ ] API routes per `docs/05-api-spec.md` §Integrations: `POST /v1/integrations`, `GET /v1/integrations`, `GET /v1/integrations/{id}`, `DELETE /v1/integrations/{id}`. Implement in `api/integrations.py`.
- [ ] Validation: prometheus integration requires `url` + `auth` discriminator; reject duplicates within `(tenant_id, kind, name)`.
- [ ] Unit + integration tests.

## Out of scope

- Slack OAuth flow (spec 0010).
- The health check endpoint (`POST /v1/integrations/{id}/health-check`) — connector-aware, ships in spec 0003.
- Periodic credential rotation.
- Real KMS integration. The interface is KMS-shaped; the implementation reads from settings. A migration to real KMS is a one-class swap and intentionally not in MVP.

## Context

- `docs/01-requirements.md` §3.2, §4.5
- `docs/03-lld.md` §3
- `docs/04-data-model.md` — `integration` table
- `docs/05-api-spec.md` §Integrations
- `src/ai_sre/models/integration.py`
- `src/ai_sre/schemas/integration.py`
- `src/ai_sre/exceptions.py`

## Design

### Files to touch

- `src/ai_sre/utils/crypto.py` — implement `EnvelopeEncryptionService` (or move to `infrastructure/crypto/` if you create that package).
- `src/ai_sre/core/integration/__init__.py` — new package.
- `src/ai_sre/core/integration/service.py` — new.
- `src/ai_sre/core/integration/repository.py` — new.
- `src/ai_sre/schemas/integration.py` — implement (currently stub).
- `src/ai_sre/api/integrations.py` — implement (currently raises NotImplementedError).
- `migrations/versions/0002_integrations.py` — new.
- `tests/unit/core/test_integration_service.py` — new.
- `tests/unit/utils/test_crypto.py` — new.
- `tests/integration/test_integration_routes.py` — new.

### New / changed contracts

```python
# utils/crypto.py
class EnvelopeEncryptionService:
    def __init__(self, master_key_b64: str) -> None: ...
    def encrypt(self, plaintext: bytes) -> bytes: ...
    def decrypt(self, ciphertext: bytes) -> bytes: ...
    # encrypted blob format: 1 byte version || 12 bytes nonce || ciphertext || 16 bytes tag

# core/integration/service.py
class IntegrationService:
    def __init__(self, repo: IntegrationRepository, crypto: EnvelopeEncryptionService): ...

    async def create(
        self, tenant_id: UUID, *, kind: str, name: str, config: dict
    ) -> Integration: ...
    async def get(self, tenant_id: UUID, integration_id: UUID) -> Integration | None: ...
    async def list(self, tenant_id: UUID) -> list[Integration]: ...
    async def delete(self, tenant_id: UUID, integration_id: UUID) -> None: ...

    def _split_config(self, kind: str, config: dict) -> tuple[bytes, dict]:
        """Returns (encrypted_blob, public_dict). public_dict is shown in UI; secrets are encrypted."""
```

### Edge cases

- Duplicate `(tenant_id, kind, name)` → raise `IntegrationAlreadyExists` (add to `exceptions.py`), API returns 409.
- Unknown `kind` → reject at API layer with 422.
- Invalid prometheus auth payload (e.g. `type=bearer` with no `token`) → 422.
- Decryption failure (key rotation gone wrong) → log + raise `IntegrationCredentialDecryptionFailed`, treat integration as `unhealthy`.

## Tests

- Unit: crypto roundtrip; service create/get/list/delete; duplicate detection; config split (public has `url_host`, encrypted contains full URL + token).
- Integration: full POST → GET → DELETE flow with API key auth; tenant isolation (tenant A cannot see tenant B's integrations).

## Rollout

- Migration: Y. Creates `integration` table per data-model doc.
- Backward compat: Y (new table).
- Feature flag: none.
- Observability: log `integration.created` and `integration.deleted` at INFO with kind and name.

## Definition of done

- [ ] All scope items implemented.
- [ ] Tests passing.
- [ ] `make lint` clean.
- [ ] Migration runs on fresh DB.
- [ ] `curl` can create a prometheus integration with bearer auth and read it back (without seeing the token).

## Follow-ups

- KMS integration (separate spec; the interface is ready).
- Integration update endpoint (PATCH).
- Credential rotation.
