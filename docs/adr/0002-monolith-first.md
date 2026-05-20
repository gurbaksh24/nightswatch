# ADR-0002: Modular monolith for MVP, microservices later

**Status:** Accepted
**Date:** 2026-05-13

## Context

The HLD describes 11 logical components. We could build them as separate services from day one, or as modules within a single deployable.

## Decision

**Single deployable, multiple modules with strictly enforced internal boundaries.**

Boundaries are enforced by:
- Directory structure (`api/`, `core/`, `connectors/`, `llm/`, `delivery/`).
- One-way import rules (`api → core → {connectors,llm,delivery}`).
- Abstract interfaces (`Connector`, `DeliveryChannel`, `LLMProvider`) at module boundaries.
- A lint rule that forbids cross-module reaches that bypass the interfaces.

## Rationale

- We don't yet know which boundaries will need to flex. Microservices freeze them prematurely.
- One service is operationally simpler: one deployment, one set of logs, one trace.
- The cost of splitting later is low because the seams are well-defined now.
- MVP scale (50 concurrent investigations, 100 tenants) does not require independent scaling of components.

## When to split

Each component becomes a candidate microservice when one of these is true:

| Component | Trigger to extract |
|---|---|
| Workers | When investigation volume requires separate autoscaling from the API. |
| LLM Gateway | When we need per-provider rate limiting and shared caching across many deployments. |
| Connector pool | When a single provider's connection pool becomes a noisy neighbour. |
| Knowledge service | When embedding throughput requires a GPU-backed sidecar. |

## Consequences

- Strong module discipline is mandatory. A loose cross-import that's a one-line fix today becomes a refactor in two years.
- We need a way to test module boundaries — a `tests/architecture/` directory verifies the import graph.
