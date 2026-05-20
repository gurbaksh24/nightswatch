# ADR-0001: Python 3.12 + FastAPI for MVP

**Status:** Accepted
**Date:** 2026-05-13
**Note:** This decision was revisited after an initial draft. Java 21 + Spring Boot 3 was seriously evaluated and produced a complete parallel design before we landed back on Python. The rationale below records why.

## Context

This platform is fundamentally an LLM/agent orchestration system that talks to external HTTP APIs (Prometheus, Slack, Anthropic). Two candidate stacks were on the table:

1. **Python 3.12 + FastAPI** — best-in-class LLM ecosystem, async-first, fast iteration loop on prompt/tool/agent code.
2. **Java 21 + Spring Boot 3** — team fluency from prior projects (GDP), mature observability and persistence, virtual threads (Project Loom) close the historical async ergonomics gap.

The technical merits are close to a coin flip for our workload — both stacks can build this system well. The deciding factors are situational.

## Decision

**Python 3.12 + FastAPI + SQLAlchemy 2 (async) + Alembic + Pydantic v2.**

Workers, API, and orchestration share the same codebase and process model (asyncio).

Specifically:

| Concern | Choice |
|---|---|
| Build / packaging | `uv` + `pyproject.toml` |
| HTTP | FastAPI (Starlette + Pydantic) |
| Concurrency | asyncio; `asyncio.gather` for parallel fan-out |
| Persistence | SQLAlchemy 2 (async) + Alembic |
| Vector store | pgvector + `pgvector-python` |
| LLM | Anthropic Python SDK, wrapped by our own `LLMGateway` |
| Job queue | Procrastinate (Postgres-backed), wrapped by `JobQueue` interface. See [ADR-0004](0004-job-queue.md). |
| Validation | Pydantic v2 at every API boundary |
| Resilience | `tenacity` for retries |
| Observability | OpenTelemetry SDK + `prometheus-client` + `structlog` |
| Testing | pytest + pytest-asyncio + Testcontainers + respx + Procrastinate's in-memory test backend |

## Rationale

- **LLM iteration speed.** The hot path of our codebase is the prompt/tool/agent layer. New LLM techniques (structured output libraries, eval harnesses, prompt-engineering utilities) reach Python first by 6–12 months. For a product whose value comes from LLM quality, this matters more than for typical CRUD work.
- **In-process embedding model.** `sentence-transformers` runs in-process, trivially. No sidecar, no hosted API needed for MVP. This is genuinely awkward in Java; the convenience here matters.
- **Tight feedback loop on agent code.** Tuning a prompt, adjusting a tool definition, re-running against a recorded incident — the Python edit-and-rerun cycle is materially faster than Java's, even with Spring Boot DevTools.
- **Less code volume** at the prompt/tool/RAG layer — roughly 30–40% less than the Java equivalent. The investigation orchestrator specifically benefits.
- **Workload fits.** asyncio handles the IO-bound workload (Prometheus queries, LLM calls, Slack posts) cleanly. The 50-concurrent-investigations target is far below any throughput concern.

## What we give up

- **Refactoring safety at scale.** Mypy strict helps a lot but isn't on par with Java's compile-time guarantees. We compensate with Pydantic at every boundary, strict typing throughout, and a high test-coverage bar.
- **JVM-grade operational maturity.** Spring Actuator + JFR + heap dumps are genuinely best-in-class. Python's OTel + Prometheus story is good, not great. We mitigate by being explicit about metrics and traces from day one, not bolting them on later.
- **Team's Java fluency advantage.** This is the real cost. Mitigation: the codebase will be conventional FastAPI / SQLAlchemy / asyncio — patterns that Java-fluent engineers can pick up quickly, especially with strong type hints throughout.

## Why we chose Python over Java despite the fluency point

The agent/LLM layer is where we'll spend the most engineering effort, where bugs hurt the most, and where ecosystem velocity matters. We're prioritising that.

The rest of the system (HTTP routing, persistence, queue, delivery) is well-trodden ground in any language. Patterns transfer easily; idioms are quickly learned.

## Consequences

- We must be diligent about asyncio: no blocking I/O in async paths, careful with sync libraries.
- mypy strict is non-negotiable. Pydantic at every boundary is non-negotiable.
- If we ever need a hot computational path (custom ML, heavy text processing), it can be carved out as a Go or Rust sidecar without affecting this decision.
- The deployment unit is a single Python image; same image runs as API (`uvicorn`) or worker (`procrastinate` worker). Selected by command, not by build.

## Revisiting

If, six months in, we hit a wall on (a) mypy not catching enough bugs in agent code, or (b) Python's operational story making production debugging painful, the parallel Java design is on disk and can be revived. The HTTP contracts, data model, and architectural decomposition are language-neutral by construction.
