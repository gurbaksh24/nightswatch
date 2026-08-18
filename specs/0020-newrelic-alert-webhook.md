# Spec: New Relic alert webhook ingestion

> New Relic workflows can notify a webhook destination when an issue opens.
> This spec adds `POST /v1/webhooks/newrelic/{tenant_id}` so those issues
> start investigations — completing the E2E loop for New Relic-only tenants
> (spec 0019 gave them the query side).

**Spec ID:** 0020
**Status:** ready-for-agent
**Author:** Gurbaksh Singh Gabbi (+ Claude)
**Created:** 2026-08-18

---

## Motivation

FR-4.x for the New Relic path. The reference client's pipeline is
YAML→Terraform→NR→Slack: NR conditions breach, issues open under per-team
policies, workflows route them. Adding our webhook as one more workflow
destination (one Terraform-managed YAML change on their side) feeds those
issues into the investigation pipeline.

---

## Scope

- [ ] `POST /v1/webhooks/newrelic/{tenant_id}`: static-token auth
      (`X-AI-SRE-Token`, constant-time compare) — New Relic destinations
      can send custom headers but cannot compute HMAC signatures.
- [ ] Reuse the integration row's `webhook_signing_secret_encrypted` for the
      token: creating a `newrelic` integration now returns a one-time
      webhook token exactly like Prometheus; the existing rotation endpoint
      works unchanged.
- [ ] `NewRelicWebhookPayload` — OUR template contract (the tenant
      configures a NR workflow custom payload template emitting these
      fields; template documented in `docs/05-api-spec.md`). Normalized to
      the Alertmanager shape so `AlertService.ingest` (fingerprint, dedupe,
      enqueue) is reused verbatim.
- [ ] `AlertService.ingest` gains `source` + `raw_override` so the alert row
      records `source="newrelic"` and the *original* NR payload (FR-4.5).
- [ ] Non-open issues (`state` ≠ ACTIVATED/CREATED/OPEN, e.g. CLOSED) are
      acknowledged with 202 `accepted: 0` and not ingested.
- [ ] Tests.

## Out of scope

- Runbook auto-ingestion from `runbook_url` (spec 0021 — the field is
  already carried into alert annotations here).
- Issue-close handling (resolving/annotating investigations on CLOSED).
- Multi-channel Slack routing by `route_group` (separate mini-spec; the
  label is preserved on the alert).

## Context

- `src/ai_sre/api/alerts.py`, `core/alert/service.py`, `schemas/alert.py`
- `src/ai_sre/api/integrations.py` (webhook-secret generation)
- `specs/0006-alert-webhook.md` (the pattern being mirrored)

## Design

### Normalization

| NR template field | becomes |
|---|---|
| `condition_name` | `labels.alertname` (required) |
| `priority` | `labels.severity` (CRITICAL→critical, HIGH→high, …) |
| `policy_name`, `route_group`, first `entity_names` | labels (`policy`, `route_group`, `entity`) |
| `title`, `nrql`, `runbook_url`, `issue_id`, extra entities | annotations |
| `labels` (template extras) | merged in (computed keys win) |
| `started_at` | `startsAt` (fallback: now) |

Fingerprinting is unchanged (`fingerprint(tenant, alertname, labels,
severity)`): repeat notifications for the same condition+entity dedupe onto
the running investigation; NR's per-occurrence `issue_id` deliberately stays
OUT of the labels so it can't break dedupe.

### Auth

Static bearer-style token in `X-AI-SRE-Token` — weaker than the
Alertmanager HMAC (no body integrity) but the strongest thing NR can send;
acceptable over TLS. Every can't-verify case collapses to one 401, matching
the Alertmanager route's no-leak behaviour.

### Edge cases

- Missing/wrong token, no newrelic integration, decrypt failure → 401.
- Malformed payload → 400 with the pydantic detail.
- `state: CLOSED` → 202 `{accepted: 0}` (workflows often notify on close).
- The webhook path is under `/v1/webhooks/`, so the spec-0017 rate limiter
  covers it automatically.

## Tests

- Unit: normalization (severity map, label/annotation layout, computed keys
  win over template extras, is-open states).
- Integration: happy path (202 → alert row `source="newrelic"` + original
  raw payload + investigation enqueued), dedupe on second post, bad/missing
  token → 401, malformed → 400, CLOSED → 202 accepted 0, create-integration
  returns the one-time token.

## Rollout

- Migrations required? N (reuses `webhook_signing_secret_encrypted`).
- Backward compatible? Y (Alertmanager path untouched; `ingest` params
  default to previous behaviour).
- Feature flag? none.
- Observability: `ai_sre_webhook_received_total{tenant_id}` counts this
  route too; alert rows are distinguishable by `source`.

## Definition of done

- [ ] Scope complete; tests passing; lint clean; docs/05 updated with the
      endpoint + a copy-pasteable NR workflow payload template.

## Follow-ups

- Spec 0021: fetch + ingest `runbook_url` into the knowledge base.
- CLOSED-state handling (auto-annotate the investigation).
