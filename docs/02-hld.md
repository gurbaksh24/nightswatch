# High-Level Design (HLD)

> Read **`01-requirements.md`** first. This doc describes the major components, their responsibilities, and how data flows between them. Internal data structures and class-level concerns live in **`03-lld.md`**.

---

## 1. System context

```
                                    ┌──────────────────────────────────────────┐
                                    │           AI-SRE Platform                │
                                    │                                          │
   Customer's stack                  │   ┌────────────┐                         │
   ─────────────────                 │   │   API /    │                         │
                                    │   │  Webhook   │◄──── Alertmanager ──────┼─── Prometheus
   ┌───────────┐    fires alert     │   │   Layer    │                         │
   │ Prometheus│ ──────────────────►│   └─────┬──────┘                         │
   │  + AM     │                    │         │                                │
   └─────┬─────┘                    │         ▼                                │
         │                          │   ┌───────────┐    ┌────────────────┐    │
         │  pulled queries          │   │ Investig.  │   │  Knowledge      │   │
         ◄──────────────────────────┼───┤ Orchestr. │◄──┤  (vector DB)    │   │
         │                          │   └─────┬──────┘   └────────────────┘    │
         │                          │         │                                │
   ┌─────┴──────┐                   │         │ tool calls                     │
   │ Customer   │                   │   ┌─────▼──────┐    ┌────────────┐       │
   │ Slack ws.  │◄──────────────────┼───┤ Connectors │    │ LLM Gateway│       │
   └────────────┘    Slack post     │   │ (Prom, …)  │    │ (Anthropic)│       │
                                    │   └────────────┘    └────────────┘       │
                                    │                                          │
                                    └──────────────────────────────────────────┘
```

---

## 2. Component overview

Each component is a logical module. In the MVP they ship as a single deployable monolith with clear internal boundaries (see [ADR-0002](adr/0002-monolith-first.md)). Each one is split-out-able into a microservice later without changing the contracts.

| # | Component | Responsibility |
|---|---|---|
| 1 | **API Layer** | HTTP surface for tenants, integrations, services, investigations, feedback. FastAPI app. |
| 2 | **Webhook Receiver** | Authenticates Alertmanager webhooks, persists raw payload, enqueues investigation. |
| 3 | **Alert Service** | Parses & normalises alerts, deduplicates, fingerprints, links to existing investigations. |
| 4 | **Investigation Orchestrator** | Runs the staged pipeline. Owns budgets, tool dispatch, retries, persistence. |
| 5 | **Connector Layer** | Pluggable adapters for telemetry sources. Prometheus only in MVP. |
| 6 | **LLM Gateway** | Wraps LLM API calls: prompt versioning, tool schemas, retries, token accounting, caching. |
| 7 | **Knowledge Service** | Chunks/embeds runbooks, postmortems, past investigations; serves vector search. |
| 8 | **Delivery Service** | Pluggable channel adapters; Slack only in MVP. |
| 9 | **Feedback Service** | Records feedback events; serves them for analytics & retraining. |
| 10 | **Worker Pool** | Async consumers (Procrastinate) that pick up investigation jobs from the queue. |
| 11 | **Persistence** | Postgres for relational data; vector DB (pgvector) for embeddings; object storage for blobs (uploads, traces). |

---

## 3. End-to-end happy path

```
1. Alertmanager POSTs alert     ──►  Webhook Receiver
2. Receiver verifies HMAC, persists raw payload, enqueues job, returns 202.
3. Procrastinate worker dequeues, hands to Investigation Orchestrator.
4. Orchestrator runs stages:
     a. Triage         — dedupe? known issue? noise?
     b. Context        — pull recent metrics, deploys, dependencies.
     c. Hypothesis     — LLM tool-loop to generate ranked hypotheses.
     d. Validation     — LLM tool-loop to confirm/refute via more queries.
     e. Report         — assemble structured RCA, score confidence.
5. Delivery Service formats and posts to Slack.
6. Feedback events arrive via Slack interactions → Feedback Service.
```

A failure or timeout in any stage produces a **partial result** with reduced confidence, not a total failure (see FR-5.2).

---

## 4. Data model summary

Full schema in **`04-data-model.md`**. Key entities:

- `tenant` — top-level isolation boundary.
- `api_key` — auth credentials for a tenant.
- `integration` — connection to an external system (Prometheus, Slack). Credentials encrypted.
- `service` — the subject service for a tenant (one per tenant in MVP).
- `service_dependency` — discovered or manually edited dependency edges.
- `metric_catalog_entry` — discovered metrics for a tenant's subject service.
- `alert` — raw + normalised alert payload received from a webhook.
- `investigation` — one investigation run; links to alert(s).
- `investigation_stage` — per-stage execution record (timing, status, output).
- `tool_call` — every LLM tool invocation with inputs/outputs/latency.
- `report` — final structured RCA output of an investigation.
- `feedback` — user feedback on an investigation.
- `knowledge_doc` — uploaded runbook / postmortem.
- `knowledge_chunk` — embedded chunk of a knowledge_doc.

Every table except `tenant` itself carries a `tenant_id`.

Procrastinate manages its own queue tables (`procrastinate_jobs`, `procrastinate_periodic_defers`, etc.) — these live in the same Postgres but are not part of our domain schema. Migrations for them are run via the Procrastinate CLI; our Alembic migrations don't touch them.

---

## 5. Async architecture

```
HTTP request (webhook)   ─────►  procrastinate.enqueue(run_investigation, id)
                                                       │
                                                       ▼
                                                Procrastinate worker
                                                       │
                                                       ▼
                                          InvestigationOrchestrator.run(id)
```

**Queue choice:** **Procrastinate** (Postgres-backed). Reasons:

1. No additional infrastructure dependency on day one — uses the same Postgres.
2. asyncio-native — workers are async functions that share connection pools, HTTP clients, and structured-logging context with the rest of the codebase.
3. Postgres-level leasing via `SELECT … FOR UPDATE SKIP LOCKED` — reliable, well-understood semantics.
4. Throughput targets (~50 concurrent investigations) are far below Procrastinate's capacity ceiling.
5. Easy to replace later — workers consume from an abstract `JobQueue` interface, not from Procrastinate directly. See [ADR-0004](adr/0004-job-queue.md).

**Worker pool:** Async Python workers (asyncio) running the same codebase as the API, started via `python -m ai_sre.workers.investigation_worker`. Horizontal scale by adding more worker processes / pods; Procrastinate handles leasing across them.

**Future escape hatch:** if the orchestrator code starts looking like a reluctant workflow engine (durable state, replay, versioning), **Temporal** is the natural landing point — *not* a bigger queue. Procrastinate stays the queue; Temporal would replace the orchestrator. See [ADR-0004](adr/0004-job-queue.md) for that reasoning.

---

## 6. The investigation pipeline (conceptual)

```
┌──────────┐   ┌──────────┐   ┌──────────────┐   ┌──────────────┐   ┌────────┐
│  Triage  │──►│ Context  │──►│  Hypothesis  │──►│  Validation  │──►│ Report │
│          │   │ Assembly │   │ (tool-loop)  │   │ (tool-loop)  │   │        │
└──────────┘   └──────────┘   └──────────────┘   └──────────────┘   └────────┘
     │              │                │                  │                │
     ▼              ▼                ▼                  ▼                ▼
  Dedupe?       Recent           N hypotheses        Top hypos        Structured
  Known         deploys,         with initial         validated        RCA
  issue?        topology,        evidence             or rejected      + confidence
  Noise?        SLOs                                  via more
                                                     queries
```

Each stage:
- Reads from `InvestigationContext` (a typed bag passed through the pipeline).
- Writes structured output back into the context.
- Persists an `investigation_stage` row.
- Has its own token, time, and tool-call budget.

**Why this shape over "ask the LLM everything"?**

- Stages **scope the work** so each LLM call has a focused task and bounded context.
- Stages **gate the budget** — we can kill an investigation that's spiralling.
- Stages **emit audit trail** at natural boundaries so failures are debuggable.
- Stages **localise prompt changes** — improving hypothesis quality doesn't risk regressing the report formatter.

See [ADR-0003](adr/0003-investigation-as-agent.md) for the agent-vs-rigid-pipeline trade-off.

### Durability principle

**The investigation row in Postgres is the source of truth, not the queue message.** The orchestrator can be invoked with just an `investigation_id` and reconstruct what stage to resume at by reading from the DB. The queue is only a delivery mechanism. This is what makes the system robust against queue bugs and worker crashes — losing a job message is recoverable; corrupting the DB isn't.

Practically:
- Each stage persists its result to `investigation_stage` before returning.
- On worker restart, the orchestrator checks `investigation_stage` rows and skips already-completed stages.
- Queue retries are idempotent because every stage is idempotent on its input.

---

## 7. LLM tool design

The Hypothesis and Validation stages are agentic: the LLM is given a set of tools and asked to reason through them. Stages call the LLM Gateway, which:

1. Sends the system prompt + tool schemas + current state.
2. Receives tool-call requests, dispatches them to the relevant connector/service.
3. Loops up to a configured max iterations.
4. Returns the final structured output.

Tool registry is the only place tools are defined (`llm/tools.py`). Adding a tool is one new entry plus a handler — no prompt rewriting.

---

## 8. Connector layer

```python
class Connector(ABC):
    async def health_check(self) -> ConnectorHealth: ...
    async def discover_topology(self, service: Service) -> Topology: ...
    async def discover_metrics(self, service: Service) -> list[MetricEntry]: ...
    async def query(self, query: ConnectorQuery) -> ConnectorResult: ...
```

The orchestrator never calls Prometheus directly. It calls `connector_registry.for_tenant(tenant_id).query(...)`. This is the seam that lets us add Datadog/Loki/etc.

Each tool the LLM has access to is implemented in terms of the connector interface, not in terms of any specific provider.

---

## 9. Delivery layer

```python
class DeliveryChannel(ABC):
    async def deliver(self, report: Report, channel_config: dict) -> DeliveryReceipt: ...
    async def handle_callback(self, payload: dict) -> CallbackResult: ...
```

`handle_callback` is how interactive feedback (Slack buttons) lands back into the platform.

---

## 10. Knowledge service

- **Vector store:** pgvector in the same Postgres instance for MVP (one less moving part).
- **Embedding model:** configurable behind an `EmbeddingProvider` interface. Default for MVP: `sentence-transformers` (`bge-small-en-v1.5`) running in-process. Switchable to a hosted embedding API (Voyage, OpenAI, Bedrock Titan) per tenant if data-residency or model-quality concerns arise.
- **Chunking:** heading-aware Markdown chunker with ~500 token chunks and 50-token overlap; preserves headings as chunk metadata.

---

## 11. Security boundaries

| Boundary | Mechanism |
|---|---|
| Tenant ↔ Tenant | Row-level filtering enforced via a `TenantScopedRepository` base; every query is tenant-scoped or it fails. A unit test sweeps the repository layer for `select(Model)` patterns that bypass the base. |
| Customer cred ↔ Disk | Envelope-encrypted with a KMS-managed key. Plaintext exists only in memory. |
| LLM provider ↔ Customer data | Per-tenant redaction config; PII-bearing fields are masked before prompt assembly. |
| Webhook ↔ Spoofing | HMAC-SHA256 over the body with a per-tenant signing secret. |
| Outbound Prometheus | Sandboxed: query timeout, max series, max points. PromQL is constructed from a typed query intent — the LLM cannot inject arbitrary PromQL. |

---

## 12. Deployment topology (MVP)

```
                  ┌─────────────┐
   internet ────► │  Load Bal.  │
                  └──────┬──────┘
                         │
              ┌──────────┴──────────┐
              │                     │
        ┌─────▼─────┐         ┌─────▼─────┐
        │ API pod   │   ...   │ API pod   │   (FastAPI / uvicorn, stateless)
        └───────────┘         └───────────┘
                         │
                         ▼  (jobs in queue)
              ┌──────────┴──────────┐
              │                     │
        ┌─────▼─────┐         ┌─────▼─────┐
        │ Worker pod│   ...   │ Worker pod│   (Procrastinate workers)
        └───────────┘         └───────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │  Postgres (+pgvector)│
              │  Object storage      │
              │  Secrets (KMS)       │
              └──────────────────────┘
```

Single region, single AZ acceptable for MVP. Multi-AZ Postgres recommended even at MVP for durability. Same container image runs as API or worker, selected by command.

---

## 13. Failure modes & responses

| Failure | Behaviour |
|---|---|
| Prometheus unreachable mid-investigation | Tool call returns typed error; orchestrator records failed tool call; LLM informed it can't query; final report flags reduced confidence. `tenacity`-based retry with exponential backoff per connector. |
| LLM provider down or rate-limited | Gateway retries with exponential backoff; if exhausted, investigation completes with a stub report ("we received your alert but couldn't complete diagnosis — here's what we did gather") and the failure is alerted internally. |
| Worker crashes mid-investigation | Procrastinate's lease expires; another worker picks up the same job. Stage-level idempotency means completed stages are skipped on re-entry. On the 3rd consecutive failure the job is marked `failed` and posted to Slack as a notice. |
| Slack delivery fails | Retried with backoff; persisted as `report.delivered_at = null` if all retries exhausted; visible in dashboard with a "Retry delivery" button. |
| LLM produces malformed structured output | Gateway re-prompts once with a corrective system message; if still malformed, falls back to text-only output with a "structured parse failed" flag. |

---

## 14. What gets built first (build order)

1. Tenant + API key + auth (the spine; everything depends on it).
2. Integration model + Prometheus connector (read-only `query` and `health_check`).
3. Subject service + topology discovery.
4. Alert webhook + persistence + dedupe.
5. Investigation orchestrator skeleton (stages that return placeholder data).
6. LLM Gateway + tool registry (with 1 real tool: `query_prometheus`).
7. Hypothesis stage end-to-end against a real alert.
8. Slack delivery (one-way, no interactivity).
9. Validation stage.
10. Report stage + dashboard read endpoints.
11. Slack interactive callbacks + feedback.
12. Knowledge ingestion + `search_runbooks` tool.
13. Backtest endpoint.
14. Hardening: rate limits, budgets, observability of the platform itself.

A working end-to-end "alert → Slack RCA" — even a thin one — should exist by step 8.
