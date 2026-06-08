# Spec 0005: Topology + metric catalog discovery

> Discover the subject service's dependencies and metric catalog from Prometheus. Runs at registration and on a schedule. Critical for hypothesis quality — without a metric catalog the LLM hypothesises against names it can't query.

**Spec ID:** 0005
**Status:** ready-for-agent
**Depends on:** 0003, 0004

---

## Motivation

FR-1.5 (dependency discovery), FR-3.1–3.3 (metric catalog + refresh schedule).

## Scope

- [ ] `MetricCatalogDiscovery` in `connectors/prometheus/catalog.py`:
  - Query `/api/v1/label/__name__/values` filtered by the service's label selector.
  - For each metric, sample a recent point to discover label keys.
  - Persist results to `metric_catalog_entry`.
- [ ] `TopologyDiscovery` (same file or sibling):
  - Inspect common request/upstream labels (`upstream`, `dst`, `downstream_service`, `peer`, `target_service` — configurable list).
  - Emit `ServiceDependency` rows with `direction` and `confirmed_by_user=False`.
- [ ] Two Procrastinate periodic tasks:
  - `refresh_metric_catalog` — every 6h per tenant.
  - `refresh_topology` — every 6h per tenant.
- [ ] API endpoints: `GET /v1/services/{id}/topology`, `PATCH /v1/services/{id}/topology`, `POST /v1/services/{id}/topology/refresh`, `GET /v1/services/{id}/metrics`, `POST /v1/services/{id}/metrics/refresh`.
- [ ] On service registration, enqueue an immediate refresh of both.
- [ ] Tests.

## Out of scope

- ML-based topology inference (we use label conventions only).
- Cross-tenant metric catalog deduplication / sharing.
- Detection of removed metrics (we update `last_seen_at`; pruning is a separate concern).

## Context

- `docs/01-requirements.md` §3.3
- `docs/03-lld.md` §10.1
- `docs/04-data-model.md` — `service_dependency`, `metric_catalog_entry`
- Spec 0003 (Prometheus connector)
- Spec 0004 (Service registration)

## Design

### Files to touch

- `src/ai_sre/connectors/prometheus/catalog.py` — implement.
- `src/ai_sre/core/service/topology_service.py` — new.
- `src/ai_sre/core/service/catalog_service.py` — new.
- `src/ai_sre/workers/app.py` — register `refresh_metric_catalog` and `refresh_topology`. Add periodic schedule via Procrastinate's `periodic` API.
- `src/ai_sre/queue/procrastinate_queue.py` — extend `_KIND_TO_TASK_NAME`.
- `src/ai_sre/api/services.py` — implement the remaining routes.
- `migrations/versions/0005_topology_catalog_indexes.py` — only if any indexes need adding.
- Tests under `tests/unit/connectors/`, `tests/integration/`.

### New / changed contracts

```python
class MetricCatalogDiscovery:
    def __init__(self, connector: Connector) -> None: ...
    async def discover(self, service: Service) -> list[MetricCatalogEntry]: ...

class TopologyDiscovery:
    def __init__(self, connector: Connector, conventions: TopologyConventions) -> None: ...
    async def discover(self, service: Service) -> list[ServiceDependency]: ...

@dataclass(frozen=True)
class TopologyConventions:
    upstream_labels: tuple[str, ...] = ("upstream", "client", "source_service")
    downstream_labels: tuple[str, ...] = ("dst", "target", "downstream_service", "peer_service")
```

### Edge cases

- Metric with > 1000 distinct label-value combinations → store top 50 by frequency, set `labels["__truncated__"] = true`.
- Same dependency discovered from multiple labels → dedupe by `(direction, name)` per service.
- User has previously confirmed a dependency; refresh finds it absent → keep but flag `last_seen_at` as stale.
- Refresh runs while a previous refresh is in-flight → idempotent; Procrastinate's task deduplication handles this if configured, otherwise use an advisory lock.

## Tests

- Unit: catalog discovery against fixture responses with various label cardinalities.
- Unit: topology discovery from label conventions.
- Integration: registration → topology refresh job runs → GET /topology returns expected dependencies.

## Rollout

- Migration: **required.** `service_dependency` and `metric_catalog_entry` were
  modelled in ORM since spec 0004 but never migrated (only `service` existed).
  Migration `0005_topology_catalog` creates both, and adds `last_seen_at` +
  `UniqueConstraint(service_id, direction, name)` to `service_dependency` and
  `UniqueConstraint(service_id, metric_name)` to `metric_catalog_entry`.
- Periodic-task schedules deployed at app start (idempotent): a single
  `refresh_all_discovery` periodic task (cron `0 */6 * * *`) fans out per
  service. Workers must consume the `discovery` queue (added to the worker
  entrypoint).
- Observability: discovery counts are emitted on the structured logs
  (`catalog.refresh.complete` / `topology.refresh.complete`, with
  `discovered=`). The dedicated Prometheus counters
  (`aisre_catalog_metrics_discovered`, `aisre_topology_edges_discovered`) are
  deferred to spec 0017 (Hardening), where `/metrics` exposition and the metric
  registry conventions land — nothing else in the codebase emits Prometheus
  metrics yet.

## Definition of done

- [ ] All scope items implemented.
- [ ] After running on a Prometheus with 100 metrics, the catalog has 100 entries within 10 seconds.
- [ ] Topology auto-fills correctly for a fake service with `upstream` and `dst` labels.

## Follow-ups

- Confidence scoring on discovered dependencies.
- Tenant-configurable convention overrides.
