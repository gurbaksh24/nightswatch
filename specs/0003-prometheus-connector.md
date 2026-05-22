# Spec 0003: Connector ABC + Prometheus connector + health check

> The `Connector` abstraction is the seam between the orchestrator and external telemetry sources. This spec lands it with a single working implementation (Prometheus) and wires the async health-check endpoint.

**Spec ID:** 0003
**Status:** ready-for-agent
**Depends on:** 0001, 0002

---

## Motivation

FR-2.1, FR-2.2, FR-2.5: the platform connects to Prometheus and verifies connectivity. NFR-8.1: adding a new connector must require implementing one interface.

## Scope

- [ ] `Connector` ABC, `ConnectorKind` enum, `ConnectorQuery` tagged union, `ConnectorResult`, `ConnectorHealth` records in `connectors/base.py` (already stubbed).
- [ ] `ConnectorRegistry` in `connectors/registry.py` — looks up the right Connector instance per (tenant, integration).
- [ ] `PrometheusConnector` in `connectors/prometheus/connector.py`:
  - HTTP client (`httpx.AsyncClient`) configured per-integration.
  - Bearer / Basic / None auth.
  - `health_check()` against `/-/healthy`.
  - `query(PromQLQuery)` against `/api/v1/query` and `/api/v1/query_range`.
  - Enforces query timeout, max_points, max_series from settings.
- [ ] `POST /v1/integrations/{id}/health-check` endpoint that enqueues a health check job via `JobQueue`.
- [ ] A new Procrastinate task `run_health_check` registered in `workers/app.py`.
- [ ] Tests including a `respx` mock of Prometheus.

## Out of scope

- Topology discovery / metric catalog (spec 0005).
- LogQuery / TraceQuery / EventQuery implementations — keep them as the typed variants that raise `ConnectorUnsupported`.
- Per-tenant connection pool tuning.
- Connector for Datadog / Loki / etc.

## Context

- `docs/01-requirements.md` §3.2, §4.5, §4.8
- `docs/03-lld.md` §10
- `src/ai_sre/connectors/base.py` (existing stub)
- `src/ai_sre/connectors/registry.py` (existing stub)
- `src/ai_sre/connectors/prometheus/*` (existing stubs)
- `src/ai_sre/queue/base.py` — for the health-check enqueue path

## Design

### Files to touch

- `src/ai_sre/connectors/base.py` — finalise types.
- `src/ai_sre/connectors/registry.py` — implement.
- `src/ai_sre/connectors/prometheus/connector.py` — implement.
- `src/ai_sre/api/integrations.py` — implement `health-check` route.
- `src/ai_sre/workers/app.py` — register `run_health_check` task.
- `src/ai_sre/queue/procrastinate_queue.py` — add `"health_check"` to `_KIND_TO_TASK_NAME`.
- `tests/unit/connectors/test_prometheus_connector.py` — new.
- `tests/integration/test_integration_health_check.py` — new.

### New / changed contracts

```python
# connectors/base.py
class ConnectorKind(StrEnum):
    PROMETHEUS = "prometheus"

@dataclass(frozen=True)
class ConnectorHealth:
    healthy: bool
    latency_ms: int
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

# tagged union (Python 3.12 PEP 695 type alias)
type ConnectorQuery = PromQLQuery | LogQuery | TraceQuery | EventQuery

@dataclass(frozen=True)
class PromQLQuery:
    intent: QueryIntent | None = None
    raw_promql: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    step: timedelta | None = None
    # validation: exactly one of intent / raw_promql

@dataclass(frozen=True)
class ConnectorResult:
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    error: str | None = None

# connectors/registry.py
class ConnectorRegistry:
    async def for_tenant(self, tenant_id: UUID) -> Connector: ...
    async def health_check_for(self, tenant_id: UUID, integration_id: UUID) -> ConnectorHealth: ...
```

### Edge cases

- Prometheus returns 4xx → `ConnectorResult(success=False, error="..")`.
- Network unreachable / DNS failure → `ConnectorTimeout` after settings.prom_query_timeout_seconds.
- Response contains > max_series → trim and set `data["truncated"]=True`; log warning.
- Integration is `status=disabled` → `ConnectorRegistry.for_tenant` raises `IntegrationUnhealthy`.
- Bearer token present but malformed → log + treat as auth failure (no token leak in logs; log the prefix only).

## Tests

- Unit: PrometheusConnector against respx-mocked Prometheus — happy path query, range query, 401, 500, timeout, max_series clamp.
- Unit: registry caches the Connector instance per (tenant, integration) and invalidates on integration update.
- Integration: `POST /v1/integrations/{id}/health-check` enqueues a job; worker (running in-process via Procrastinate's test backend) updates `integration.status` and `last_health_check_at`.

## Rollout

- Migration: N (no new schema).
- Backward compat: Y.
- Observability: emit `aisre_connector_query_total{kind,outcome}` and `aisre_connector_query_duration_seconds`.

## Definition of done

- [ ] All scope items implemented.
- [ ] Tests passing.
- [ ] `make lint` clean.
- [ ] Against a local Prometheus container, the health check endpoint flips status from `pending` → `healthy`.

## Follow-ups

- TLS / mTLS auth.
- A `GET /v1/integrations/{id}/preview` endpoint that runs a sample query (debugging tool).
