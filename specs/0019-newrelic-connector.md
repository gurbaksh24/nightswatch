# Spec: New Relic connector (query side)

> First non-Prometheus telemetry connector, exercising NFR-8.1's seam: a
> tenant connects their New Relic account and investigations can query it via
> NRQL (typed intents, no raw-query injection), discover its metric catalog,
> and pull topology from NerdGraph entity relationships. Alert ingestion FROM
> New Relic is spec 0020.

**Spec ID:** 0019
**Status:** ready-for-agent
**Author:** Gurbaksh Singh Gabbi (+ Claude)
**Created:** 2026-08-18

---

## Motivation

FR-2.5 / NFR-8.1: adding a telemetry provider must be "implement `Connector`
+ register", no orchestrator changes. A real client runs YAML→Terraform→New
Relic→Slack; for their tenants the LLM must query New Relic the way it
queries Prometheus today. Coexistence decision: a tenant MAY have both
connected at once; discovery/validation prefer Prometheus, fall back to New
Relic.

---

## Scope

- [ ] `ConnectorKind.NEWRELIC`; `RawNRQL` intent; `NRQLQuery` wrapper in the
      `ConnectorQuery` union.
- [ ] `connectors/newrelic/` — `queries.py` (intent→NRQL builder + raw-NRQL
      safety validator) and `connector.py` (NerdGraph client: health check,
      query, metric discovery, topology via entity relationships,
      selector probe).
- [ ] `Connector.probe_selector(selector)` — non-abstract ABC method
      (default: `ConnectorUnsupported`); New Relic implements it. Lets
      `core/` validate a selector without importing a concrete connector.
- [ ] Registry build branch for `kind="newrelic"`.
- [ ] `POST /v1/integrations` accepts `kind="newrelic"` with
      `{account_id, api_key, region}` (discriminated union); public view is
      `{account_id, region}` — the API key never leaves encryption.
- [ ] Migration: widen `ck_integration_kind` to include `'newrelic'`.
- [ ] Catalog / topology refresh + service-selector validation try
      Prometheus first, then New Relic (both-at-once coexistence).
- [ ] New LLM tool `query_newrelic` (same intent surface as
      `query_prometheus`, `raw_nrql` escape hatch), registered for
      hypothesis + validation.
- [ ] Tests.

## Out of scope

- Alert ingestion from New Relic workflows (spec 0020) and the webhook
  auth token that goes with it.
- Runbook auto-ingestion from alert payloads (spec 0021).
- Merging catalogs/topology from BOTH providers for one service (first
  configured provider wins per refresh).
- Log/trace NRQL tools (the connector can run them via raw NRQL; dedicated
  `LogQuery` support is a follow-up).

## Context

- `src/ai_sre/connectors/base.py`, `registry.py`, `prometheus/*` (the shape
  to mirror), `connectors/README.md`
- `src/ai_sre/core/service/{service,catalog_service,topology_service}.py`
- `src/ai_sre/schemas/integration.py`, `core/integration/service.py`,
  `api/integrations.py`, `models/integration.py`
- `src/ai_sre/llm/tools/query_prometheus.py`
- NerdGraph: `POST https://api.newrelic.com/graphql` (EU:
  `api.eu.newrelic.com`), header `API-Key`, NRQL via
  `actor.account(id).nrql(query).results`.

## Design

### Files to touch

- `src/ai_sre/connectors/base.py` — kind, `RawNRQL`, `NRQLQuery`,
  `METRICS_CONNECTOR_KINDS`, `probe_selector` default.
- `src/ai_sre/connectors/newrelic/{__init__,queries,connector}.py` — new.
- `src/ai_sre/connectors/registry.py` — `_build_from_row` branch.
- `src/ai_sre/schemas/integration.py` — discriminated create union.
- `src/ai_sre/core/integration/service.py` — `_public_view` branch.
- `src/ai_sre/models/integration.py` + `migrations/versions/0013_*.py`.
- `src/ai_sre/core/service/*.py` — provider fallback loop.
- `src/ai_sre/llm/tools/{__init__,query_newrelic}.py`.
- `docs/05-api-spec.md`, `connectors/README.md`.
- Tests (below).

### Intent → NRQL mapping (deliberate approximations, documented in code)

| Intent | NRQL |
|---|---|
| `rate_over_window` | `SELECT rate(sum(metric), 1 minute) FROM Metric WHERE ... SINCE Ns ago` |
| `percentile` | `SELECT percentile(metric, p*100) FROM Metric ...` |
| `change_over_time` | `SELECT max(metric) - min(metric) FROM Metric ...` (delta approx.) |
| `aggregation(op, by, inner)` | op applied to inner's metric, `FACET` for `by` |
| `raw_nrql` | validated: single statement, starts with SELECT, ≤4096 chars |

Explicit `start`/`end` become `SINCE <epoch> UNTIL <epoch>`; otherwise the
intent's window (`SINCE Ns ago`). Label selectors become backticked
`WHERE`-clause equality conjunctions with escaped values.

### Edge cases

- GraphQL-level `errors` → failed `ConnectorResult`, never an exception.
- HTTP timeout → `ConnectorTimeout`; non-200 → failed result.
- `probe_selector` parses `uniqueCount(metricName)` out of the result row so
  "0 matching series" is distinguishable from "query returned one row".
- Topology parse failures (NerdGraph shape drift) → warn log + empty edges,
  never a failed refresh.
- Discovery status literal `no_prometheus` is kept for API compatibility but
  now means "no metrics backend at all" (comment at the source).

## Tests

- Unit: NRQL builder (each intent, escaping, raw validator accept/reject);
  connector via respx (health ok/error, query success/GraphQL error/timeout,
  discovery parse, probe); tool handler happy/no-integration/failed paths;
  registry builds a NewRelicConnector from an encrypted row.
- Integration: create a `newrelic` integration (201, public view hides the
  key), 422 on malformed config; kinds constraint migration applies.

## Rollout

- Migrations required? Y (`0013`, constraint-only, instant).
- Backward compatible? Y.
- Feature flag? none — inert until a tenant connects New Relic.
- Observability: connector calls flow through the existing tool-call audit +
  `ai_sre_tool_calls_total{tool="query_newrelic"}`.

## Definition of done

- [ ] Scope complete; tests written and passing; `make lint` clean.
- [ ] Docs updated (05-api-spec, connectors README).
- [ ] Migration runs cleanly on fresh + production-shape DB.
- [ ] No new dependencies.

## Follow-ups

- Spec 0020: New Relic alert webhook ingestion (static-token auth, payload
  template).
- Spec 0021: runbook auto-ingestion from alert `runbook_url`.
- Multi-channel Slack routing by `route_group`.
