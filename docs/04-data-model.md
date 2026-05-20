# Data Model

> All tables (except `tenant`) carry `tenant_id` and have a composite index on `(tenant_id, …)` for tenant-scoped queries.

---

## Conventions

- Primary keys: UUIDv7 (time-ordered).
- Timestamps: `created_at`, `updated_at` on every table, UTC, `TIMESTAMPTZ`.
- Soft delete: `deleted_at TIMESTAMPTZ NULL` where applicable.
- Enums: stored as `TEXT` with a `CHECK` constraint; mirrored as Python enums.
- JSON: `JSONB` for everything; never `JSON`.

---

## Entities

### `tenant`

| col | type | notes |
|---|---|---|
| `id` | `UUID` PK | UUIDv7 |
| `name` | `TEXT` NOT NULL | display name |
| `slug` | `TEXT` UNIQUE NOT NULL | url-friendly |
| `status` | `TEXT` NOT NULL | `active`, `suspended`, `deleting` |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | |

### `api_key`

| col | type | notes |
|---|---|---|
| `id` | `UUID` PK | |
| `tenant_id` | `UUID` FK → `tenant.id` | |
| `name` | `TEXT` | label for the key |
| `key_hash` | `TEXT` UNIQUE | SHA-256 of the key |
| `prefix` | `TEXT` | first 8 chars of the key, shown in UI |
| `last_used_at` | `TIMESTAMPTZ` NULL | |
| `expires_at` | `TIMESTAMPTZ` NULL | |
| `revoked_at` | `TIMESTAMPTZ` NULL | |

### `integration`

| col | type | notes |
|---|---|---|
| `id` | `UUID` PK | |
| `tenant_id` | `UUID` FK | |
| `kind` | `TEXT` | `prometheus`, `slack`, future: `loki`, `datadog`, … |
| `name` | `TEXT` | tenant-defined label |
| `config_encrypted` | `BYTEA` | envelope-encrypted JSON blob: URL, auth, etc. |
| `config_public` | `JSONB` | non-secret config (URL host, etc.) for display |
| `status` | `TEXT` | `pending`, `healthy`, `unhealthy`, `disabled` |
| `last_health_check_at` | `TIMESTAMPTZ` NULL | |
| `webhook_signing_secret_encrypted` | `BYTEA` NULL | per-tenant secret for inbound webhooks (kind=prometheus only) |

Unique constraint: `(tenant_id, kind, name)`.

### `service`

The subject service for a tenant. **MVP: one per tenant.**

| col | type | notes |
|---|---|---|
| `id` | `UUID` PK | |
| `tenant_id` | `UUID` FK | UNIQUE in MVP (one service per tenant) |
| `name` | `TEXT` | human name |
| `label_selector` | `JSONB` | e.g. `{"service": "checkout"}` |
| `ownership` | `JSONB` NULL | team, slack channel, oncall — used in reports |
| `slo_config` | `JSONB` NULL | optional SLO definitions |

### `service_dependency`

Edges discovered between services in the customer's stack.

| col | type | notes |
|---|---|---|
| `id` | `UUID` PK | |
| `tenant_id` | `UUID` FK | |
| `service_id` | `UUID` FK → `service.id` | the subject service |
| `direction` | `TEXT` | `upstream` (calls subject) / `downstream` (called by subject) |
| `name` | `TEXT` | discovered service name |
| `metadata` | `JSONB` | how it was discovered, label selectors, etc. |
| `confirmed_by_user` | `BOOLEAN` NOT NULL DEFAULT FALSE | |

### `metric_catalog_entry`

| col | type | notes |
|---|---|---|
| `id` | `UUID` PK | |
| `tenant_id` | `UUID` FK | |
| `service_id` | `UUID` FK | |
| `metric_name` | `TEXT` | e.g. `http_requests_total` |
| `metric_type` | `TEXT` | `counter` / `gauge` / `histogram` / `summary` / `unknown` |
| `labels` | `JSONB` | observed label keys + sample values |
| `unit` | `TEXT` NULL | parsed from `_seconds`, `_bytes`, etc. |
| `help_text` | `TEXT` NULL | from `# HELP` |
| `last_seen_at` | `TIMESTAMPTZ` | when last seen during a catalog refresh |

Index: `(tenant_id, service_id, metric_name)`.

### `alert`

Raw + normalised alerts received from webhooks.

| col | type | notes |
|---|---|---|
| `id` | `UUID` PK | |
| `tenant_id` | `UUID` FK | |
| `source` | `TEXT` | `alertmanager` / future others |
| `received_at` | `TIMESTAMPTZ` | |
| `raw_payload` | `JSONB` | full body for audit |
| `alert_name` | `TEXT` | parsed |
| `severity` | `TEXT` NULL | |
| `status` | `TEXT` | `firing` / `resolved` |
| `started_at` | `TIMESTAMPTZ` | from payload |
| `ended_at` | `TIMESTAMPTZ` NULL | |
| `labels` | `JSONB` | |
| `annotations` | `JSONB` | |
| `fingerprint` | `TEXT` | dedupe key (see LLD §6) |
| `investigation_id` | `UUID` FK → `investigation.id` NULL | set after dedupe/link |

Index: `(tenant_id, fingerprint, received_at DESC)`.

### `investigation`

| col | type | notes |
|---|---|---|
| `id` | `UUID` PK | |
| `tenant_id` | `UUID` FK | |
| `service_id` | `UUID` FK | |
| `triggering_alert_id` | `UUID` FK → `alert.id` | first alert that triggered it |
| `fingerprint` | `TEXT` | inherited from triggering alert |
| `status` | `TEXT` | `pending`, `running`, `succeeded`, `partial`, `failed` |
| `confidence` | `TEXT` NULL | `low` / `medium` / `high` |
| `started_at` | `TIMESTAMPTZ` NULL | |
| `completed_at` | `TIMESTAMPTZ` NULL | |
| `budget_snapshot` | `JSONB` | consumed budget at completion |
| `prompt_version` | `TEXT` | git sha or semver of prompt set used |
| `code_version` | `TEXT` | git sha of code |

Index: `(tenant_id, status, started_at DESC)`.

### `investigation_stage`

| col | type | notes |
|---|---|---|
| `id` | `UUID` PK | |
| `tenant_id` | `UUID` FK | |
| `investigation_id` | `UUID` FK | |
| `name` | `TEXT` | `triage` / `context` / `hypothesis` / `validation` / `report` |
| `attempt` | `INT` NOT NULL DEFAULT 1 | for retries |
| `status` | `TEXT` | `pending`/`running`/`succeeded`/`failed`/`timed_out`/`budget_exhausted` |
| `started_at` / `completed_at` | `TIMESTAMPTZ` NULL | |
| `output` | `JSONB` NULL | stage-specific structured output |
| `error` | `JSONB` NULL | error info if failed |

Unique: `(investigation_id, name, attempt)`.

### `tool_call`

| col | type | notes |
|---|---|---|
| `id` | `UUID` PK | |
| `tenant_id` | `UUID` FK | |
| `investigation_id` | `UUID` FK | |
| `stage_id` | `UUID` FK → `investigation_stage.id` | |
| `tool_name` | `TEXT` | |
| `input` | `JSONB` | |
| `output` | `JSONB` NULL | truncated if > 1 MB; full output in object storage |
| `output_object_key` | `TEXT` NULL | object-storage pointer for large outputs |
| `latency_ms` | `INT` | |
| `outcome` | `TEXT` | `success` / `error` / `timeout` / `budget_blocked` |
| `error` | `JSONB` NULL | |
| `created_at` | `TIMESTAMPTZ` | |

Index: `(investigation_id, created_at)`.

### `report`

| col | type | notes |
|---|---|---|
| `id` | `UUID` PK | |
| `tenant_id` | `UUID` FK | |
| `investigation_id` | `UUID` FK UNIQUE | one report per investigation |
| `schema_version` | `TEXT` | for forward compat |
| `headline` | `TEXT` | 1-line diagnosis |
| `confidence` | `TEXT` | `low`/`medium`/`high` |
| `hypotheses` | `JSONB` | structured list with evidence refs |
| `next_actions` | `JSONB` | structured suggestions |
| `related_incidents` | `JSONB` | refs to past investigations |
| `delivered_at` | `TIMESTAMPTZ` NULL | when first successfully delivered |
| `delivery_receipts` | `JSONB` | per-channel delivery info |

### `feedback`

| col | type | notes |
|---|---|---|
| `id` | `UUID` PK | |
| `tenant_id` | `UUID` FK | |
| `investigation_id` | `UUID` FK | |
| `kind` | `TEXT` | `useful` / `not_useful` / `actual_cause` / `comment` |
| `actor` | `TEXT` NULL | Slack user id or email |
| `body` | `TEXT` NULL | for `comment` / `actual_cause` |
| `metadata` | `JSONB` | |
| `created_at` | `TIMESTAMPTZ` | |

### `knowledge_doc`

| col | type | notes |
|---|---|---|
| `id` | `UUID` PK | |
| `tenant_id` | `UUID` FK | |
| `kind` | `TEXT` | `runbook` / `postmortem` / `past_investigation` |
| `title` | `TEXT` | |
| `source_object_key` | `TEXT` NULL | original blob if uploaded |
| `metadata` | `JSONB` | author, date, tags, etc. |
| `created_at` | `TIMESTAMPTZ` | |
| `deleted_at` | `TIMESTAMPTZ` NULL | soft delete |

### `knowledge_chunk`

| col | type | notes |
|---|---|---|
| `id` | `UUID` PK | |
| `tenant_id` | `UUID` FK | |
| `doc_id` | `UUID` FK → `knowledge_doc.id` | |
| `ord` | `INT` | chunk order within doc |
| `text` | `TEXT` | |
| `headings` | `JSONB` | parent heading path |
| `embedding` | `vector(384)` | pgvector |
| `tokens` | `INT` | |

Index: HNSW on `embedding` filtered by `tenant_id`.

### Queue tables (Procrastinate-managed)

Procrastinate owns its own queue schema (`procrastinate_jobs`, `procrastinate_periodic_defers`, etc.) in the same Postgres. These are NOT part of our domain model and are NOT touched by our Alembic migrations — Procrastinate's CLI manages them.

Our code only ever talks to the queue through the `JobQueue` interface (LLD §15.1). If we ever swap Procrastinate out, these tables go away and the implementation behind `JobQueue` changes; nothing else does.

---

## Indexing summary

- Every FK has an index.
- Every `tenant_id`-prefixed table has at least one `(tenant_id, …)` index supporting the most common access pattern.
- `alert.fingerprint`, `investigation.fingerprint`: indexed for dedupe lookup.
- `knowledge_chunk.embedding`: HNSW index.
- `job(status, available_at)`: worker dequeue.

---

## Retention

| Table | Retention |
|---|---|
| `alert` | 30 days minimum; configurable per tenant. |
| `tool_call.output` (large) | object-storage outputs: 90 days. |
| `investigation`, `report`, `feedback` | indefinite, until tenant deletion. |
| Procrastinate's completed jobs | retained 7 days, then purged by a Procrastinate maintenance task. |

Tenant deletion is a 7-day soft-delete: tenant status → `deleting`, all data fenced from reads, hard-delete job runs after grace period.
