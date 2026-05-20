# Low-Level Design (LLD)

> Read **`02-hld.md`** first. This doc fills in module-level contracts, classes, and key algorithms.

---

## 1. Source layout

```
src/ai_sre/
├── main.py                  # FastAPI app entrypoint
├── config.py                # Pydantic Settings (env-driven)
├── db.py                    # SQLAlchemy engine, session, Base
├── exceptions.py            # Typed exception hierarchy
│
├── api/                     # HTTP routes only; thin
│   ├── deps.py              # FastAPI dependencies (auth, tenant resolution)
│   ├── tenants.py
│   ├── integrations.py
│   ├── services.py
│   ├── alerts.py            # webhook receiver
│   ├── investigations.py
│   └── feedback.py
│
├── core/                    # Domain logic; HTTP-agnostic
│   ├── tenant/
│   ├── alert/
│   ├── investigation/
│   │   ├── orchestrator.py
│   │   ├── pipeline.py
│   │   ├── context.py
│   │   ├── budget.py
│   │   └── stages/
│   │       ├── triage.py
│   │       ├── context_assembly.py
│   │       ├── hypothesis.py
│   │       ├── validation.py
│   │       └── report.py
│   ├── knowledge/
│   └── feedback/
│
├── connectors/              # External data-source adapters
│   ├── base.py              # Connector ABC, ConnectorQuery, ConnectorResult
│   ├── registry.py          # Per-tenant connector lookup
│   └── prometheus/
│       ├── connector.py
│       ├── queries.py       # Typed PromQL builders
│       └── catalog.py       # Metric discovery
│
├── llm/                     # LLM Gateway + tool registry
│   ├── gateway.py
│   ├── tools.py             # Tool definitions + dispatch
│   └── prompts/
│       ├── system.py
│       ├── triage.py
│       ├── hypothesis.py
│       └── validation.py
│
├── delivery/                # Output channels
│   ├── base.py              # DeliveryChannel ABC
│   └── slack.py
│
├── models/                  # SQLAlchemy ORM models
├── schemas/                 # Pydantic DTOs (request/response)
├── workers/                 # Procrastinate app + worker entrypoints
│   ├── app.py               # Procrastinate App + task registrations
│   └── investigation_worker.py
├── queue/                   # JobQueue interface + Procrastinate adapter
│   ├── base.py
│   └── procrastinate_queue.py
└── utils/
    ├── logging.py
    ├── ids.py
    └── crypto.py
```

**Rule:** `api` ↔ `core` is a one-way dependency. `core` does not import from `api`. `core` ↔ `connectors`/`llm`/`delivery` are also one-way (core depends on their interfaces, not their implementations — wired via the registries). `queue/base.py` is the seam; `queue/procrastinate_queue.py` is the only place that imports Procrastinate.

---

## 2. Configuration

`config.py` uses Pydantic `BaseSettings`. Every config value:

- Has a default appropriate for local dev.
- Is documented with a one-line description.
- Is loaded from `AI_SRE_*` env vars.

Categories:
- **App:** env, debug, host, port.
- **DB:** url, pool size.
- **Security:** API key signing secret, tenant secret encryption key (KMS key ARN or local key).
- **LLM:** provider, model, api key, max tokens per investigation, max cost USD per investigation.
- **Investigation:** stage budgets (timeouts in seconds), max tool calls per stage, dedupe window.
- **Connectors:** default Prometheus query timeout, max points, max series.
- **Slack:** client id, client secret, signing secret.
- **Queue:** Procrastinate concurrency, lease duration, retry policy.

---

## 3. Database session & repositories

```python
# db.py
engine = create_async_engine(settings.db_url, pool_size=...)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]: ...
```

Every repository inherits a base that **forces tenant scoping**:

```python
# core/_base/repository.py
class TenantScopedRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: AsyncSession, tenant_id: TenantId) -> None:
        self.session = session
        self.tenant_id = tenant_id

    def _scoped(self, stmt: Select[T]) -> Select[T]:
        # Inject WHERE tenant_id = :tenant_id
        return stmt.where(self.model.tenant_id == self.tenant_id)

    async def get(self, id_: UUID) -> ModelT | None: ...
    async def list(self, **filters: Any) -> Sequence[ModelT]: ...
```

A unit test verifies no `select(Model)` in repositories bypasses `_scoped` by AST-walking the `core/` package.

---

## 4. Authentication & tenant resolution

`api/deps.py`:

```python
async def current_tenant(
    authorization: str = Header(...),
    session: AsyncSession = Depends(get_session),
) -> TenantContext:
    """Parse Bearer token, look up API key, return tenant context."""
```

Every protected route declares `tenant: TenantContext = Depends(current_tenant)` — there is no implicit tenant.

The webhook endpoint uses a **different** auth dep: `current_tenant_from_webhook_signature`, which validates the HMAC.

For workers (no HTTP request), the orchestrator's `run(investigation_id)` loads the investigation, derives the tenant_id, and sets a contextvar that the repository layer reads.

---

## 5. Webhook receiver

```python
# api/alerts.py
@router.post("/v1/webhooks/alertmanager/{tenant_id}")
async def alertmanager_webhook(
    tenant_id: UUID,
    request: Request,
    x_signature: str = Header(..., alias="X-AI-SRE-Signature"),
) -> AlertReceiveResponse:
    raw = await request.body()
    tenant = await verify_signature_and_load(tenant_id, raw, x_signature)
    payload = AlertmanagerPayload.model_validate_json(raw)
    alert_ids = await alert_service.ingest(tenant, payload, raw)
    return AlertReceiveResponse(accepted=len(alert_ids), alert_ids=alert_ids)
```

`alert_service.ingest` is fast: persist + enqueue via `JobQueue.enqueue(...)`. No LLM calls. No outbound HTTP. Target p95 < 100ms.

---

## 6. Alert service & deduplication

Each alert in the payload becomes one `alert` row. **Fingerprint** is computed deterministically:

```
fingerprint = sha256(
    tenant_id || alert_name || sorted(labels_excluding_unstable) || severity
)
```

`unstable` labels = `["instance", "pod", "node", "container_id", ...]` — configurable per tenant.

The Alert Service then runs the dedupe logic:

```
If an investigation exists for this fingerprint
   and was started within `dedupe_window`
   and is in {PENDING, RUNNING, RECENTLY_COMPLETED}:
   → link the alert to it (update `alert_count`, last_seen_at).
Else:
   → create a new investigation in PENDING state and enqueue.
```

---

## 7. Investigation orchestrator

```python
# core/investigation/orchestrator.py
class InvestigationOrchestrator:
    def __init__(
        self,
        pipeline: Pipeline,
        repo: InvestigationRepository,
        delivery: DeliveryDispatcher,
    ) -> None: ...

    async def run(self, investigation_id: UUID) -> None:
        """Run the full pipeline. Idempotent: safe to retry."""
        inv = await self.repo.get(investigation_id)
        ctx = await self._build_context(inv)
        await self.repo.mark_running(inv)
        try:
            for stage in self.pipeline.stages:
                if await self.repo.is_stage_complete(inv.id, stage.name):
                    # Reload its persisted output into ctx and skip.
                    await self._restore_stage(ctx, stage.name)
                    continue
                await self._run_stage(stage, ctx)
            await self._finalize(inv, ctx)
        except BudgetExhausted as e:
            await self._finalize_partial(inv, ctx, reason=str(e))
        except Exception as e:
            await self._finalize_failed(inv, ctx, error=e)
        await self.delivery.dispatch(inv, ctx.report)
```

**Idempotency is the contract.** `run` checks `investigation_stage` rows on entry and skips completed stages, restoring their outputs into the context. This makes the orchestrator safe under:

- Procrastinate retries.
- Worker crashes mid-stage.
- Manual replay via `POST /v1/investigations/{id}/replay`.

The DB row is the source of truth. The queue message is just delivery.

---

## 8. Pipeline & stages

```python
# core/investigation/pipeline.py
class Stage(Protocol):
    name: str
    timeout_seconds: int
    max_tool_calls: int

    async def execute(self, ctx: InvestigationContext) -> StageResult: ...

@dataclass
class Pipeline:
    stages: list[Stage]

    @classmethod
    def default(cls) -> Pipeline:
        return cls(stages=[
            TriageStage(),
            ContextAssemblyStage(),
            HypothesisStage(),
            ValidationStage(),
            ReportStage(),
        ])
```

### 8.1 InvestigationContext

```python
@dataclass
class InvestigationContext:
    tenant: TenantContext
    investigation_id: UUID
    alert: NormalisedAlert
    service: Service
    dependencies: Topology
    metric_catalog: MetricCatalog
    budget: Budget                # mutates as stages consume

    # Stage outputs (filled in order)
    triage: TriageResult | None = None
    context: ContextSummary | None = None
    hypotheses: list[Hypothesis] = field(default_factory=list)
    validated: list[ValidatedHypothesis] = field(default_factory=list)
    report: Report | None = None

    # Audit trail
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
```

### 8.2 Stages

Each stage has its own README under `core/investigation/stages/` describing inputs, outputs, prompts, and tests.

- **TriageStage** — deterministic-first: check dedupe table, check "known issues" list (similar alerts in past 24h), check noise heuristics (flapping). Only invokes LLM if classification is ambiguous.
- **ContextAssemblyStage** — fans out to connectors in parallel via `asyncio.gather`: recent deploys, recent error rate, dependency health, related metrics. Pure data collection, no LLM.
- **HypothesisStage** — LLM tool-loop. Asks model to generate up to N (default 5) ranked hypotheses. Tools: `query_prometheus`, `list_metric_names`, `get_service_dependencies`, `search_past_incidents`.
- **ValidationStage** — LLM tool-loop. For each top-K hypothesis (default 3), asks the model: "what query would confirm or refute this?" then runs it. Updates hypothesis confidence based on result.
- **ReportStage** — non-agentic LLM call: takes validated hypotheses + evidence + alert + context, emits structured `Report` JSON via tool-use constrained generation.

---

## 9. Budget & cost control

```python
@dataclass
class Budget:
    max_wall_seconds: int = 300
    max_tool_calls: int = 30
    max_llm_tokens: int = 200_000
    max_llm_cost_usd: float = 0.50

    elapsed_seconds: float = 0
    tool_calls_used: int = 0
    tokens_used: int = 0
    cost_used_usd: float = 0

    def assert_can_call_tool(self) -> None: ...
    def record_tool_call(self, latency_s: float) -> None: ...
    def record_llm_call(self, tokens: int, cost_usd: float) -> None: ...
```

`assert_can_*` raises `BudgetExhausted`, which the orchestrator catches and uses to finalize partial results.

---

## 10. Connector layer

```python
# connectors/base.py
class Connector(ABC):
    kind: ClassVar[ConnectorKind]

    @abstractmethod
    async def health_check(self) -> ConnectorHealth: ...

    @abstractmethod
    async def discover_topology(self, service: Service) -> Topology: ...

    @abstractmethod
    async def discover_metrics(self, service: Service) -> list[MetricEntry]: ...

    @abstractmethod
    async def query(self, query: ConnectorQuery) -> ConnectorResult: ...
```

`ConnectorQuery` is a tagged union:

```python
ConnectorQuery = (
    PromQLQuery
    | LogQuery       # stubbed; raises NotImplemented in MVP
    | TraceQuery     # stubbed
    | EventQuery     # stubbed
)
```

The Prometheus connector handles only `PromQLQuery`. Other connectors (when added) can support any subset of query types.

### 10.1 Prometheus connector

- HTTP client: `httpx.AsyncClient` with per-tenant connection pool.
- Query timeout, max samples, max series enforced via Prometheus query params + post-validation.
- Auth: bearer token, basic, mTLS, or none — selected per-integration.
- Catalog discovery: queries `/api/v1/label/__name__/values` filtered by the service's label selectors, then samples each metric's labels.

### 10.2 PromQL safety

The LLM **never** sees raw PromQL it can copy. Instead it sees high-level intents:

```python
QueryIntent = (
    RateOverWindow(metric, labels, window)
    | Aggregation(op, by, inner)
    | Percentile(p, metric, labels, window)
    | ChangeOverTime(metric, labels, window)
)
```

The connector translates `QueryIntent` to PromQL via vetted builders in `prometheus/queries.py`. The LLM can ask for a raw query via a `raw_promql` field, but it goes through a parser that rejects unsafe constructs (no `topk` over unbounded series, no recording-rule writes, etc.).

---

## 11. LLM Gateway

```python
# llm/gateway.py
class LLMGateway:
    async def chat(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_format: ResponseFormat | None = None,
        budget: Budget,
    ) -> LLMResponse: ...

    async def tool_loop(
        self,
        *,
        system: str,
        user: str,
        tools: list[ToolSpec],
        tool_dispatcher: ToolDispatcher,
        budget: Budget,
        max_iterations: int = 10,
    ) -> LLMResponse: ...
```

- **Provider abstraction:** under the hood, a `LLMProvider` ABC with an `AnthropicProvider` impl. (OpenAI/Bedrock are straightforward additions.)
- **Token & cost accounting:** every call updates the budget.
- **Caching:** prompt-prefix caching when the provider supports it.
- **Prompt versioning:** every prompt is in `llm/prompts/` as a constant and a `PROMPT_VERSION` string. The version is recorded on every `tool_call` and `report` row for reproducibility.

---

## 12. Tools

```python
# llm/tools.py
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict        # JSON Schema
    handler: ToolHandler

ToolHandler = Callable[[ToolCallInput, InvestigationContext], Awaitable[ToolCallOutput]]

REGISTRY: dict[str, ToolSpec] = {}

def register(spec: ToolSpec) -> None: ...
def for_stage(stage: str) -> list[ToolSpec]: ...
```

Per-stage tool subsets are defined in `pipeline.py`. The Hypothesis stage gets read-only tools; Validation gets the same plus targeted query tools.

---

## 13. Knowledge service

```python
# core/knowledge/service.py
class KnowledgeService:
    async def ingest(self, tenant: TenantContext, doc: KnowledgeDocInput) -> KnowledgeDoc: ...
    async def search(self, tenant: TenantContext, query: str, k: int = 5) -> list[KnowledgeChunk]: ...
    async def ingest_past_investigation(self, investigation: Investigation) -> None: ...
```

- Chunking: heading-aware Markdown chunker; falls back to fixed-token chunks for PDF / plain text.
- Embeddings: `EmbeddingProvider` ABC; default `BGESmallEmbedder` (local, `sentence-transformers`) for cost. Swappable to a hosted embedding API if needed.
- Storage: `knowledge_chunk` table with `embedding vector(384)` (pgvector).

---

## 14. Delivery — Slack

```python
# delivery/slack.py
class SlackDelivery(DeliveryChannel):
    async def deliver(self, report: Report, cfg: SlackChannelConfig) -> DeliveryReceipt: ...
    async def handle_callback(self, payload: dict) -> CallbackResult: ...
```

Message format uses Block Kit. Layout:

1. **Header block**: 🚨 + alert name + confidence badge.
2. **Section**: headline diagnosis (1–2 sentences).
3. **Section (collapsed)**: triggering alert details.
4. **Divider**.
5. For each top hypothesis (up to 3):
   - Section: hypothesis statement.
   - Context: evidence bullets with `query result` snippets (truncated).
6. **Section**: suggested next actions.
7. **Section**: links — full investigation, past similar incidents.
8. **Actions**: 👍 / 👎 / 🎯 / "View trace".

Callbacks for buttons hit `POST /v1/delivery/slack/callback` which routes to `SlackDelivery.handle_callback`.

---

## 15. Queue & workers

### 15.1 The seam: `JobQueue`

The orchestrator and webhook code talk to `JobQueue`, never Procrastinate directly. This is the only abstraction over the queue:

```python
# queue/base.py
class JobQueue(Protocol):
    async def enqueue(
        self,
        kind: str,
        payload: dict,
        *,
        delay_seconds: int = 0,
    ) -> str:                           # returns job id
        ...

    async def cancel(self, job_id: str) -> None: ...
```

There is intentionally no `dequeue` on this interface — workers register handler functions with Procrastinate at startup, and Procrastinate calls them. We expose just enough to enqueue and cancel.

### 15.2 Procrastinate adapter & worker

`workers/app.py` defines the Procrastinate `App` and registers task handlers:

```python
# workers/app.py
from procrastinate import App, PsycopgConnector

procrastinate_app = App(
    connector=PsycopgConnector(conninfo=settings.db_url_psycopg),
)

@procrastinate_app.task(name="run_investigation", retry=3, queue="investigations")
async def run_investigation_task(investigation_id: str) -> None:
    orchestrator = await build_orchestrator()  # from app-startup registry
    await orchestrator.run(UUID(investigation_id))
```

`queue/procrastinate_queue.py` is the `JobQueue` implementation:

```python
# queue/procrastinate_queue.py
class ProcrastinateJobQueue(JobQueue):
    def __init__(self, app: procrastinate.App) -> None:
        self.app = app

    async def enqueue(self, kind, payload, *, delay_seconds=0) -> str:
        task = self.app.tasks[f"run_{kind}"]
        job = await task.defer_async(**payload, schedule_in={"seconds": delay_seconds})
        return str(job)
```

### 15.3 Worker entrypoint

```bash
python -m ai_sre.workers.investigation_worker
```

Which runs:

```python
# workers/investigation_worker.py
async def main() -> None:
    async with procrastinate_app.open_async():
        await procrastinate_app.run_worker_async(
            queues=["investigations"],
            concurrency=settings.queue_concurrency,
        )
```

Concurrency is set via `AI_SRE_QUEUE_CONCURRENCY` (default: 10). With 5 worker pods at concurrency=10, the system handles 50 concurrent investigations — meeting NFR-2.4 with one pod's headroom.

### 15.4 Retry & failure handling

- Procrastinate's `retry=3` handles transient failures (e.g. Postgres blip, LLM provider 5xx). Backoff is exponential.
- Stage-level idempotency (LLD §7) means a retry resumes from the last completed stage rather than starting over — this is what keeps the cost of a retry small.
- After max retries, the job is marked `failed` in Procrastinate's table. A separate dashboard view surfaces these for ops. The investigation row is also marked `failed`, and a "delivery failed" Slack notice is posted.

### 15.5 Future swap

If we ever outgrow Procrastinate or want true workflow semantics, the migration is:

1. Replace `ProcrastinateJobQueue` with a new `JobQueue` impl. No orchestrator changes.
2. **Or** — if the orchestrator itself starts being the pain point (durable replay, versioning, complex retry policies per stage) — replace the orchestrator with a Temporal workflow. The stages become Temporal activities. See [ADR-0004](adr/0004-job-queue.md) for the reasoning.

---

## 16. Observability of the platform

- **Logging:** structlog with JSON output; `tenant_id` and `investigation_id` set via contextvar at orchestrator entry.
- **Tracing:** OpenTelemetry SDK; one span per FastAPI request, one per stage, one per tool call, one per LLM call.
- **Metrics:** Prometheus exposition at `/metrics`. Key metrics:
  - `ai_sre_investigation_duration_seconds{stage}`
  - `ai_sre_tool_calls_total{tool,outcome}`
  - `ai_sre_llm_tokens_total{provider,model}`
  - `ai_sre_llm_cost_usd_total{provider,model}`
  - `ai_sre_webhook_received_total{tenant_id}`
  - `ai_sre_delivery_total{channel,outcome}`
  - `ai_sre_queue_depth{queue}` — Procrastinate-sourced.

---

## 17. Testing strategy

- **Unit:** every pure-logic module. Pipeline stages are unit-tested with a fake LLM Gateway that returns scripted responses.
- **Integration:** Postgres + a fake Prometheus (`prometheus-mock` fixture), end-to-end alert → report. Procrastinate runs in-process for tests via `procrastinate.testing`.
- **Contract tests:** for each connector — recorded VCR-style cassettes of real Prometheus responses.
- **Eval suite:** a directory `evals/` with frozen (alert, expected-RCA) pairs. CI runs the eval against the prompt set and reports quality regressions.
- **Replay:** the backtest endpoint is also a debugging tool — any production investigation can be re-run with the current code.

---

## 18. Migration strategy

- **DB migrations:** Alembic for our schema. Procrastinate migrations are managed by Procrastinate's own CLI and run in the same deploy step (a pre-start hook on the API container).
- **Backward compat:** report JSON has a `schema_version` field. Reader code must handle older versions for at least one year.
- **Prompt changes:** every change to a prompt bumps `PROMPT_VERSION` and is recorded on rows it influenced.

---

## 19. Key sequence diagrams

### 19.1 Alert ingestion (synchronous part)

```
Alertmanager ──► POST /v1/webhooks/alertmanager/{tid}
                       │
                       ▼
                AlertReceiver
                       │
              verify HMAC ─┐
                       │   └──► reject 401
                       │
                  parse payload ─┐
                       │         └──► reject 400
                       │
                       ▼
                AlertService.ingest
                       │
                ┌──────┴──────┐
                │             │
        persist alert    fingerprint
                │             │
                └─────┬───────┘
                      │
                  dedupe lookup
                      │
              ┌───────┴───────┐
              │               │
      link to existing    create investigation
              │               │
              └───────┬───────┘
                      │
                  job_queue.enqueue("investigation", {investigation_id})
                      │
                      ▼
                202 Accepted
```

### 19.2 Investigation run

```
Procrastinate worker ──► invokes run_investigation_task(investigation_id)
                                │
                                ▼
                        Orchestrator.run(id)
                                │
                                ├──► load investigation, alert, service, topology
                                ├──► build InvestigationContext
                                │
                                ├──► for stage in pipeline.stages:
                                │       if stage already complete: restore output, skip
                                │       else: execute and persist
                                │
                                │       TriageStage          (deterministic + maybe LLM)
                                │       ContextAssemblyStage (parallel connector calls)
                                │       HypothesisStage      (LLM tool-loop)
                                │       ValidationStage      (LLM tool-loop per hypo)
                                │       ReportStage          (structured-output LLM)
                                │
                                ├──► persist report
                                │
                                └──► DeliveryDispatcher.dispatch
                                       └─ Slack post (Block Kit)
```
