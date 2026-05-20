# API Specification

> All routes are versioned under `/v1/`. All responses are JSON unless explicitly noted. Authentication is Bearer token (API key) unless explicitly noted. All protected endpoints are tenant-scoped via the API key.

---

## Conventions

- Errors return `{ "error": { "code": "...", "message": "...", "details": {...} } }` with appropriate 4xx/5xx status.
- Resources include `id`, `created_at`, `updated_at`.
- Pagination: `?limit=&cursor=`. Responses include `next_cursor` when applicable.
- All IDs are UUIDs in canonical hyphenated form.

---

## Auth

### `POST /v1/auth/api-keys` (admin only)
Create a new API key for the calling tenant.

**Response 201**
```json
{ "id": "...", "key": "ai-sre-live-...", "prefix": "ai-sre-l", "created_at": "..." }
```
The `key` value is returned only once.

### `GET /v1/auth/api-keys`
List API keys (without secret).

### `DELETE /v1/auth/api-keys/{id}`
Revoke a key.

---

## Tenants

### `GET /v1/tenant`
Return the calling tenant's profile.

### `PATCH /v1/tenant`
Update tenant fields (`name`, etc.).

---

## Integrations

### `POST /v1/integrations`
Create an integration.

**Request**
```json
{
  "kind": "prometheus",
  "name": "Production Prometheus",
  "config": {
    "url": "https://prom.example.com",
    "auth": { "type": "bearer", "token": "..." }
  }
}
```

**Response 201**
```json
{
  "id": "...",
  "kind": "prometheus",
  "name": "Production Prometheus",
  "status": "pending",
  "config_public": { "url_host": "prom.example.com" },
  "created_at": "..."
}
```
The integration health is checked asynchronously; poll `GET /v1/integrations/{id}` for status.

### `GET /v1/integrations`
List integrations.

### `GET /v1/integrations/{id}`

### `POST /v1/integrations/{id}/health-check`
Force a re-check.

### `DELETE /v1/integrations/{id}`

### Slack OAuth

#### `GET /v1/integrations/slack/oauth/start`
Redirects to Slack OAuth.

#### `GET /v1/integrations/slack/oauth/callback`
Slack OAuth callback. Persists the integration.

---

## Services

### `POST /v1/services`
Register the subject service (one per tenant in MVP).

**Request**
```json
{
  "name": "Checkout",
  "label_selector": { "service": "checkout" },
  "ownership": { "team": "checkout-platform", "slack_channel": "#oncall-checkout" }
}
```

### `GET /v1/services/{id}`

### `GET /v1/services/{id}/topology`
Return discovered upstream/downstream services with `confirmed_by_user` flags.

### `PATCH /v1/services/{id}/topology`
Confirm or edit dependencies.

### `POST /v1/services/{id}/topology/refresh`
Trigger an immediate topology refresh.

### `GET /v1/services/{id}/metrics`
Return the metric catalog. Supports `?filter=` substring filter and pagination.

### `POST /v1/services/{id}/metrics/refresh`

---

## Alerts (Webhook ingress)

### `POST /v1/webhooks/alertmanager/{tenant_id}`

**Auth:** NOT Bearer. Uses header `X-AI-SRE-Signature: sha256=<hex>` over the raw body, using the tenant's webhook signing secret.

**Request:** Alertmanager webhook v4+ payload (verbatim).

**Response 202**
```json
{ "accepted": 3, "alert_ids": ["...", "...", "..."] }
```

---

## Investigations

### `GET /v1/investigations`
List investigations. Filters: `?status=&service_id=&from=&to=&confidence=`.

### `GET /v1/investigations/{id}`
Full investigation with current status and (if available) the report.

### `GET /v1/investigations/{id}/trace`
Full audit trail: stages, prompts, tool calls, LLM responses, errors.

### `GET /v1/investigations/{id}/report`
The structured RCA report.

### `POST /v1/investigations/{id}/replay`
Re-run the investigation with current code/prompts. Creates a new investigation linked via `replayed_from`.

### `POST /v1/investigations/backtest`
Submit a raw Alertmanager payload (or a reference to a stored alert) without it actually firing through a webhook. Useful for evaluating prompts and onboarding demos.

**Request**
```json
{
  "alert_payload": { /* Alertmanager v4 payload */ },
  "service_id": "...",
  "dry_run": false
}
```
With `dry_run=true`, no Slack delivery occurs.

---

## Feedback

### `POST /v1/investigations/{id}/feedback`
**Request**
```json
{
  "kind": "useful",
  "actor": "user@example.com",
  "body": "Nailed it — DB connection pool exhaustion."
}
```

### `GET /v1/investigations/{id}/feedback`

---

## Knowledge

### `POST /v1/knowledge/docs`
Upload a runbook or postmortem. Accepts `multipart/form-data` with file plus JSON metadata.

### `GET /v1/knowledge/docs`

### `GET /v1/knowledge/docs/{id}`

### `DELETE /v1/knowledge/docs/{id}`

### `POST /v1/knowledge/search` (internal — used by tools)
**Request**
```json
{ "query": "connection pool exhaustion checkout", "k": 5 }
```

---

## Delivery callbacks

### `POST /v1/delivery/slack/callback`
**Auth:** Slack request signing (per Slack docs), not Bearer.

Handles interactive component callbacks (👍/👎/🎯/View Trace buttons).

---

## Admin / health

### `GET /healthz`
Liveness — returns 200 if process is running.

### `GET /readyz`
Readiness — checks DB, queue, secrets manager.

### `GET /metrics`
Prometheus metrics exposition.

---

## Error codes

| code | meaning |
|---|---|
| `auth.invalid_token` | API key missing/invalid/revoked |
| `auth.invalid_signature` | Webhook signature mismatch |
| `validation.failed` | Request body failed validation |
| `tenant.not_found` | Tenant not found or suspended |
| `integration.unhealthy` | Connectivity test failed |
| `service.already_exists` | Tenant already has a service (MVP cap) |
| `investigation.not_found` | |
| `rate_limited` | |
| `budget.exhausted` | Cost or token budget exhausted |
| `internal` | Generic 500 |

---

## Webhook payload — signature scheme

Header: `X-AI-SRE-Signature: sha256=<hex>`

Computation:
```
hmac_sha256(key=tenant.webhook_signing_secret, msg=raw_request_body) → hex
```
Constant-time comparison. Replays are rejected if the timestamp annotation in the body is older than 5 minutes (when present).

---

## OpenAPI

The full OpenAPI spec is generated from FastAPI at `/openapi.json` and rendered at `/docs` (Swagger UI) and `/redoc`.
