# Spec 0017: Hardening — rate limits, observability, full budget enforcement

> The "make it production-shaped" pass before turning it on for real users.

**Spec ID:** 0017
**Status:** ready-for-agent
**Depends on:** 0006 (rate limit on webhook), 0008 (budget), 0010 (rate limit on delivery)

---

## Motivation

NFR-1.1, NFR-1.2 (perf), NFR-6.1–6.2 (cost), NFR-7.1–7.3 (observability).

## Scope

- [ ] Rate-limit webhook ingress: `slowapi` (Redis-free, in-memory + Postgres backed) — per-tenant 100 req/min default, configurable.
- [ ] Per-tenant LLM cost cap (NFR-6.1) and per-investigation cap — enforced in `LLMGateway.chat`.
- [ ] Per-investigation tool call cap (NFR-6.2) — enforced in `ToolDispatcher`.
- [ ] OTel auto-instrumentation: FastAPI, SQLAlchemy, httpx. Manual spans on stages + tools.
- [ ] Structured logs include `tenant_id`, `investigation_id`, `stage`, `trace_id` via contextvars.
- [ ] `/metrics` endpoint exposes all the documented metrics.
- [ ] `/healthz` (already exists) and `/readyz` actually check DB + queue.
- [ ] Tests.

## Out of scope

- Per-user (not per-tenant) rate limits — out of scope for MVP.
- Distributed tracing exporter configuration (use the env var pattern; deployers configure).

## Context

- `docs/01-requirements.md` §4 (full NFRs)
- `docs/03-lld.md` §16

## Design

### Files to touch

- `src/ai_sre/middleware/rate_limit.py` — new.
- `src/ai_sre/main.py` — wire OTel auto-instrumentation, rate limiter middleware, readiness check.
- `src/ai_sre/utils/logging.py` — contextvars-driven binding.
- `src/ai_sre/observability/metrics.py` — new central place for `prometheus_client` Counter/Histogram singletons.
- Tests.

### Edge cases

- Rate limit triggered on webhook → 429 with `Retry-After`. Importantly: persist the limit hit (rare but high-signal).
- LLM provider rate limit (different from ours) → tenacity retry as in spec 0008.

## Tests

- Unit: rate limit triggers at boundary.
- Unit: cost cap blocks an over-budget chat call.
- Integration: `/metrics` returns the expected gauge / counter set.

## Definition of done

- [ ] Scope complete.
- [ ] All NFR-7.* metrics present at `/metrics`.
- [ ] One Grafana dashboard JSON (`ops/grafana/aisre.json`) included as a starting point for ops.
