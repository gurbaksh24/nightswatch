# Spec 0006: Alert webhook + dedupe + enqueue

> The hot path: Alertmanager fires, we 202 within 500ms and an investigation is in the queue.

**Spec ID:** 0006
**Status:** ready-for-agent
**Depends on:** 0001, 0002, 0004

---

## Motivation

FR-4.1–4.5: the primary ingress. Everything downstream depends on this working reliably.

## Scope

- [ ] Webhook signature verification (HMAC-SHA256 over body, constant-time compare). Per-tenant signing secret stored on the Prometheus integration's `webhook_signing_secret_encrypted` column.
- [ ] A way to issue / show / rotate that secret: extend `POST /v1/integrations` to optionally generate one (or add `POST /v1/integrations/{id}/webhook-secret`).
- [ ] Parse Alertmanager v4 payload (Pydantic schema already exists in `schemas/alert.py`).
- [ ] `AlertService.ingest`:
  - For each alert in the payload: compute fingerprint, persist `alert` row, run dedupe logic, create `investigation` if needed, enqueue `run_investigation` job.
- [ ] `Fingerprinter` deterministic helper in `core/alert/fingerprinter.py`.
- [ ] Dedupe window from settings (default 15 min).
- [ ] Implement `POST /v1/webhooks/alertmanager/{tenant_id}`.
- [ ] Tests including signature failure and dedupe behaviour.

## Out of scope

- Resolved-alert handling beyond storing `status=resolved` and `ended_at`. We don't auto-close investigations on resolve in MVP.
- Per-tenant unstable-labels config UI — use a static default list with an env override for now.
- Rate limiting on the webhook endpoint (spec 0017).

## Context

- `docs/01-requirements.md` §3.4
- `docs/03-lld.md` §5, §6
- `docs/05-api-spec.md` §Alerts
- `src/ai_sre/api/alerts.py` (existing stub)
- `src/ai_sre/models/alert.py`
- `src/ai_sre/schemas/alert.py`
- `src/ai_sre/queue/base.py`

## Design

### Files to touch

- `src/ai_sre/core/alert/__init__.py` — new package.
- `src/ai_sre/core/alert/service.py` — implement.
- `src/ai_sre/core/alert/fingerprinter.py` — new.
- `src/ai_sre/core/alert/repository.py` — new.
- `src/ai_sre/api/alerts.py` — implement.
- `src/ai_sre/api/integrations.py` — extend to generate webhook secret on prometheus integration creation.
- `src/ai_sre/utils/webhook_signature.py` — new.
- `tests/unit/core/test_fingerprinter.py` — new.
- `tests/unit/core/test_alert_service.py` — new.
- `tests/integration/test_alertmanager_webhook.py` — new.

### New / changed contracts

```python
# core/alert/fingerprinter.py
def fingerprint(
    tenant_id: UUID,
    alert_name: str,
    labels: dict[str, str],
    severity: str | None,
    unstable_labels: Iterable[str] = ("instance", "pod", "node", "container_id"),
) -> str:
    """Deterministic SHA-256 hex digest. Same inputs → same output, forever."""

# core/alert/service.py
class AlertService:
    async def ingest(
        self,
        tenant_id: UUID,
        payload: AlertmanagerPayload,
        raw: bytes,
    ) -> list[UUID]:
        """For each alert: persist, fingerprint, dedupe, enqueue. Returns alert ids."""
```

### Edge cases

- Replay attack: payload received twice — dedupe by fingerprint + window, link the new `alert` row to existing investigation.
- Alert with no `labels.alertname` → reject 400.
- Investigation in `failed` status with matching fingerprint → start a new one (don't link to a failed run).
- Payload with 100 alerts — process all in one transaction; emit one investigation per unique fingerprint.
- Investigation already `running` and we enqueue again → Procrastinate accepts; orchestrator's stage-skip logic handles it.

## Tests

- Unit: fingerprint determinism + sensitivity to label changes; signature verification happy/wrong/empty.
- Unit: dedupe logic across windows.
- Integration: end-to-end POST signed by the right secret returns 202 with `alert_ids`; wrong signature returns 401; duplicate fingerprint within window doesn't create a new investigation.

## Rollout

- Migration: probably index additions on `alert(tenant_id, fingerprint, received_at DESC)` if not already present.
- Observability: `aisre_webhook_received_total{tenant_id,outcome}`, `aisre_alert_ingested_total{tenant_id}`, `aisre_investigation_created_total{tenant_id}`, p95 latency histogram.

## Definition of done

- [ ] Scope complete.
- [ ] p95 latency of the webhook endpoint < 200ms under a small load test.
- [ ] `amtool` or `curl` with a real Alertmanager v4 payload produces an `investigation` row.

## Follow-ups

- Resolved-alert linking and auto-close.
- Per-tenant unstable-label configuration.
