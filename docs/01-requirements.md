# Requirements

> **Scope of this doc:** The functional and non-functional requirements for the MVP of an AI-driven RCA / diagnosis platform. Out-of-scope items are listed explicitly at the end.

---

## 1. Product summary

An AI-driven Site Reliability Engineer ("AI-SRE") that ingests alerts from a customer's monitoring stack, performs an autonomous investigation against their telemetry, and delivers a structured Root Cause Analysis (RCA) with evidence and suggested remediation steps into Slack within minutes.

**MVP shape:**
- SaaS, multi-tenant
- Single subject service per tenant (with discovery of immediate dependencies)
- Prometheus + Alertmanager as the first integration
- Slack as the only delivery channel
- Diagnosis-only — no automated remediation
- Target time-to-first-diagnosis: 2–5 minutes

---

## 2. Personas

| Persona | Role | Primary interaction |
|---|---|---|
| **Onboarder** (Platform / DevOps lead) | Connects integrations, defines the subject service, uploads runbooks | Web UI / API for tenant configuration |
| **Oncall engineer** | Recipient of RCA when an alert fires | Reads Slack message, optionally provides feedback |
| **Engineering manager** | Reviews effectiveness of RCAs, audits investigations | Web dashboard for investigation history & quality metrics |

---

## 3. Functional requirements

### 3.1 Tenant & onboarding

- **FR-1.1** The system MUST support multiple isolated tenants. Data from one tenant MUST NOT be accessible to another under any condition.
- **FR-1.2** A tenant onboarding flow MUST allow the user to create a tenant, generate an API key for that tenant, and register the first subject service in under 15 minutes.
- **FR-1.3** A tenant MAY register exactly one "subject service" in the MVP — the service that alerts will be diagnosed against.
- **FR-1.4** A subject service is identified by a set of Prometheus label selectors (e.g. `{service="checkout"}`) plus a human-readable name.
- **FR-1.5** The system MUST auto-discover the subject service's immediate dependencies (upstream callers, downstream callees) from metric labels at onboarding time and on a scheduled refresh.

### 3.2 Integrations

- **FR-2.1** The system MUST support connecting a Prometheus instance via URL with one of: no auth, bearer token, basic auth.
- **FR-2.2** The system MUST verify connectivity at the time of integration creation and surface a clear error if the credentials or URL are invalid.
- **FR-2.3** The system MUST support connecting a Slack workspace via Slack OAuth and allowing the tenant to select the channel where RCAs are delivered.
- **FR-2.4** The system MUST persist integration credentials encrypted at rest.
- **FR-2.5** The connector layer MUST be designed so that adding new metric/log/trace providers (Datadog, Loki, Splunk, OTel, etc.) does not require changes to the investigation orchestrator.

### 3.3 Metric catalog & topology

- **FR-3.1** On Prometheus connection, the system MUST discover the metric names, label keys, and label-value cardinality relevant to the subject service.
- **FR-3.2** The metric catalog MUST be refreshed on a configurable schedule (default: every 6 hours).
- **FR-3.3** The service-dependency graph MUST be discoverable from common HTTP/RPC metric conventions and MUST be inspectable and editable by the user.

### 3.4 Alert ingestion

- **FR-4.1** The system MUST expose an HTTPS webhook endpoint per tenant that accepts the Alertmanager webhook v4+ payload.
- **FR-4.2** Webhook requests MUST be authenticated via a tenant-scoped signing secret (HMAC) or bearer token in the `Authorization` header.
- **FR-4.3** The system MUST acknowledge the webhook with a 2xx response within 1 second and process the investigation asynchronously.
- **FR-4.4** The system MUST deduplicate alerts: if an identical alert fingerprint is received within a configurable window (default: 15 minutes) and an investigation is already running or recently completed for it, the new alert MUST be linked to the existing investigation rather than starting a new one.
- **FR-4.5** The system MUST persist every received alert payload for at least 30 days for audit and debugging.

### 3.5 Investigation pipeline

- **FR-5.1** Each investigation MUST proceed through ordered stages: Triage → Context Assembly → Hypothesis → Validation → Report.
- **FR-5.2** Each stage MUST have a configurable time budget. If a stage exceeds its budget, the pipeline MUST proceed with whatever partial results were obtained and the final report MUST reflect this.
- **FR-5.3** The orchestrator MUST log every tool call (inputs, outputs, latency, success/failure) made by the LLM during an investigation.
- **FR-5.4** Every claim in the final RCA MUST be backed by at least one piece of evidence — a tool call result, a metric query, a runbook excerpt, or a past-incident reference.
- **FR-5.5** The investigation MUST produce a structured output containing: (a) headline diagnosis, (b) confidence level (low/medium/high), (c) evidence list with citations, (d) suggested next actions, (e) related past incidents, (f) the alert that triggered it.
- **FR-5.6** The investigation MUST complete and deliver its result within the configured SLA (default: 5 minutes p95).

### 3.6 LLM tool surface

The orchestrator MUST expose at minimum these tools to the LLM:

- **FR-6.1** `query_prometheus(promql, time_window)` — execute a PromQL query.
- **FR-6.2** `list_metric_names(filter)` — query the per-tenant metric catalog.
- **FR-6.3** `get_service_dependencies(service)` — return the dependency graph for a service.
- **FR-6.4** `get_recent_changes(service, window)` — return recent deployment / config change events (stub in MVP; pluggable interface).
- **FR-6.5** `search_runbooks(query)` — vector-search uploaded runbooks for the tenant.
- **FR-6.6** `search_past_incidents(query)` — vector-search past investigations and postmortems for the tenant.
- **FR-6.7** `get_alert_details()` — return the raw alert payload and parsed metadata.

### 3.7 Knowledge base

- **FR-7.1** Tenants MUST be able to upload runbooks and postmortems as Markdown, plain text, or PDF.
- **FR-7.2** Uploaded documents MUST be chunked and embedded for vector search, scoped per tenant.
- **FR-7.3** Completed investigations and their final RCAs MUST be embedded and made searchable as future "past incidents."

### 3.8 Delivery (Slack)

- **FR-8.1** On investigation completion, the system MUST post a message to the tenant's configured Slack channel.
- **FR-8.2** The Slack message MUST follow this structure:
  - Headline + confidence badge
  - Triggering alert (collapsed)
  - Top 1–3 hypotheses with evidence (each citing tool calls)
  - Suggested next actions
  - Link to full investigation in the web dashboard
  - Feedback buttons: 👍 useful / 👎 not useful / 🎯 mark actual cause
- **FR-8.3** Subsequent updates to the same investigation (e.g. higher-confidence revision) MUST update the original message thread rather than spam a new one.

### 3.9 Feedback loop

- **FR-9.1** The system MUST capture all feedback events (thumbs up/down, free text, "actual cause" annotations).
- **FR-9.2** Feedback MUST be queryable in the dashboard and exported as training/eval data.
- **FR-9.3** Negative feedback MUST surface the full investigation trace (every tool call, prompt, and LLM response) for debugging.

### 3.10 Dashboard (web UI — minimal in MVP)

- **FR-10.1** Tenants MUST be able to view a list of all investigations with filters (status, time, alert name, confidence, feedback).
- **FR-10.2** Each investigation detail page MUST show the full trace: stages, tool calls, prompts, LLM responses, final report, feedback.
- **FR-10.3** Tenants MUST be able to trigger a **backtest**: submit a past alert payload manually and see what RCA would have been produced.

---

## 4. Non-functional requirements

### 4.1 Performance

- **NFR-1.1** p95 time-to-deliver from alert ingestion → Slack post: ≤ 5 minutes.
- **NFR-1.2** p95 webhook ack latency: ≤ 500 ms.
- **NFR-1.3** Dashboard list/detail endpoints: p95 ≤ 800 ms at 10k investigations per tenant.

### 4.2 Scale (MVP targets — design should not preclude 10x)

- **NFR-2.1** Up to 100 tenants.
- **NFR-2.2** Up to 50 investigations per tenant per day (steady state); burst to 200/day.
- **NFR-2.3** Up to 1,000 active alert rules per tenant in Alertmanager.
- **NFR-2.4** Up to 50 concurrent investigations system-wide.

### 4.3 Availability

- **NFR-3.1** Webhook ingestion endpoint: 99.9% monthly availability. (Alerts MUST NOT be lost.)
- **NFR-3.2** Investigation completion: 99% (degraded outputs acceptable; total failure rare).
- **NFR-3.3** Dashboard: 99.5% monthly availability.

### 4.4 Durability & data retention

- **NFR-4.1** Alert payloads: retained 30 days minimum.
- **NFR-4.2** Investigation records, traces, feedback: retained indefinitely (subject to tenant deletion).
- **NFR-4.3** Tenant deletion MUST cascade and complete within 7 days.

### 4.5 Security

- **NFR-5.1** All inbound and outbound traffic MUST use TLS 1.2+.
- **NFR-5.2** Integration credentials MUST be encrypted at rest using envelope encryption (KMS-backed DEK).
- **NFR-5.3** API authentication MUST use tenant-scoped API keys (Bearer tokens) with rotation support.
- **NFR-5.4** Webhook signatures MUST be verified using constant-time comparison.
- **NFR-5.5** Tenant isolation MUST be enforced at the database query layer (row-level scoping); the application MUST NOT issue a query without a tenant scope.
- **NFR-5.6** Outbound Prometheus queries MUST be sandboxed: query timeout (default 10s), max series limit, max points returned. PromQL injection from LLM output MUST be impossible (use parameterised query construction).
- **NFR-5.7** No PII from log lines or metric labels MUST be transmitted to the LLM provider without tenant opt-in.

### 4.6 Cost

- **NFR-6.1** LLM token spend per investigation MUST be capped (default: $0.50). On cap breach, the investigation completes with whatever it has and is flagged.
- **NFR-6.2** Prometheus queries per investigation MUST be capped (default: 30) with the same fail-soft behaviour.

### 4.7 Observability of the platform itself

- **NFR-7.1** Every investigation MUST emit OpenTelemetry traces with one span per stage and one per tool call.
- **NFR-7.2** Standard RED metrics (Rate, Errors, Duration) MUST be exposed for every internal API and every connector.
- **NFR-7.3** Structured logs MUST always include `tenant_id` and `investigation_id` when applicable.

### 4.8 Extensibility (the most important NFR for MVP)

- **NFR-8.1** Adding a new data-source connector MUST require implementing one interface (`Connector`) and registering it; no orchestrator changes.
- **NFR-8.2** Adding a new delivery channel MUST require implementing one interface (`DeliveryChannel`); no orchestrator changes.
- **NFR-8.3** Adding a new LLM tool MUST require defining the tool schema and a handler function; no prompt rewriting.
- **NFR-8.4** Adding a new investigation stage MUST require implementing a `Stage` interface and registering it in the pipeline config.

### 4.9 Developer experience

- **NFR-9.1** Local dev MUST be runnable with `docker-compose up` and no other prerequisites.
- **NFR-9.2** Every module MUST have a `README.md` explaining its responsibility, contracts, and conventions.
- **NFR-9.3** Every public interface MUST have type annotations and docstrings.
- **NFR-9.4** The repo MUST include a `specs/` folder with a spec template, so feature work can be driven by writing a spec and handing it to an LLM coding agent.

---

## 5. Explicitly out of scope (deferred to v2+)

- Automated remediation / action execution (Tier 2/3 from the brainstorm)
- Multi-service onboarding per tenant
- Log/trace/event integrations beyond stubbed interfaces (Loki, Splunk, Datadog, Jaeger, OTel collectors)
- Custom LLM fine-tuning per tenant
- SSO / SAML (tenant admin uses email+password or magic link)
- Granular RBAC (single "admin" role per tenant in MVP)
- Chat interface ("ask questions about my infra")
- Multi-region deployment
- On-prem / customer-hosted deployment
- Mobile app

---

## 6. Success metrics

These aren't requirements per se, but the bar we'll measure against:

- **Adoption:** Time from sign-up to first delivered RCA: target ≤ 1 day, stretch ≤ 1 hour.
- **Quality:** Thumbs-up rate on delivered RCAs: target ≥ 60% within 90 days of launch.
- **Accuracy:** "Marked as actual cause" rate (top hypothesis matches real RCA): target ≥ 40%.
- **Cost:** Median LLM cost per investigation ≤ $0.20.
- **Latency:** p95 time-to-Slack ≤ 4 minutes (one minute headroom on the SLA).
