# AGENTS.md — How to work in this repo

> If you are an LLM coding agent (Claude Code, Cursor, Aider, Codex, etc.), read this file in full before touching any code. If you are a human, this file describes the conventions you and the agent will both follow.

---

## What this project is

An AI-driven SRE platform. A customer's Alertmanager fires an alert at our webhook; we investigate against their Prometheus and other telemetry, produce a structured Root Cause Analysis, and deliver it to their Slack within 5 minutes.

**Read first:**

1. [`docs/01-requirements.md`](docs/01-requirements.md) — what the system does and doesn't do.
2. [`docs/02-hld.md`](docs/02-hld.md) — components and how they fit.
3. [`docs/03-lld.md`](docs/03-lld.md) — module-level interfaces and key algorithms.
4. [`docs/04-data-model.md`](docs/04-data-model.md) — schemas.
5. [`docs/05-api-spec.md`](docs/05-api-spec.md) — HTTP surface.
6. [`docs/adr/`](docs/adr/) — why we made the architectural choices we did.

If a task asks you to do something that contradicts these docs, **stop and surface the contradiction** rather than silently following the docs or silently following the task. Update the docs as part of the same change if the contradiction reflects a real decision.

---

## Spec-driven workflow

This repo is built to be driven by **specs**: short, structured Markdown files describing one unit of work. The flow is:

```
human writes specs/NNN-feature.md  →  LLM reads spec + relevant docs/code  →  LLM produces a PR
```

To run this loop:

1. The human creates a new file under `specs/` using [`specs/TEMPLATE.md`](specs/TEMPLATE.md). Numbering is sequential (`0001`, `0002`, …).
2. The spec lists: motivation, scope, files to touch, contracts (types, signatures), tests, out-of-scope.
3. The LLM reads the spec, the referenced files, and the relevant docs. It produces code, tests, and a migration if needed.
4. Definition of done is in the spec.

**An agent must never:**

- Modify code outside the files listed in the spec without explicit permission.
- Skip writing tests for new behaviour.
- Edit a doc in `docs/` without saying so in the PR description.
- Add a new third-party dependency without listing it in the spec.

**An agent must always:**

- Read every file in the spec's "files to touch" list before writing.
- Read the README of every module it edits.
- Make types as precise as Pydantic + mypy strict allow.
- Add docstrings to public functions and classes.
- Run `make lint test` mentally before declaring done.

---

## Repo conventions

### Code style

- Python 3.12+, **mypy strict**, **ruff** for formatting and linting.
- Async-first. No blocking I/O in async functions. Wrap CPU-bound work in `asyncio.to_thread`.
- Dataclasses for internal value objects; **Pydantic** at API boundaries.
- Public functions and classes get docstrings; internal helpers don't need them but their names should be clear.
- Imports: `ruff isort` order.

### Module rules

- `api/` may import from `core/`, `schemas/`, `models/`, `utils/`, `queue/` (base only).
- `core/` may import from `models/`, `schemas/`, `utils/`, `queue/` (base only), and the **abstract base** of `connectors`, `llm`, `delivery`. It MUST NOT import a concrete implementation (e.g. `connectors.prometheus.connector`); it gets one via registry/DI.
- `connectors/`, `llm/`, `delivery/` may import from `models/`, `schemas/`, `utils/`, and their own bases. They MUST NOT import from `core/` or `api/`.
- `queue/procrastinate_queue.py` is the only file in `src/` allowed to `import procrastinate`. The rest of the codebase talks to `queue.base.JobQueue` (see ADR-0004).
- `workers/app.py` is the *worker-side* exception — it imports Procrastinate to register tasks, but it MUST NOT be imported from `core/` or `api/` (except `main.py` for lifespan wiring).
- `utils/` is a leaf: it MUST NOT import from any other in-repo module.
- See `tests/architecture/` for the automated checks.

### Naming

- Files: `snake_case.py`.
- Classes: `PascalCase`.
- Functions/methods/variables: `snake_case`.
- Constants: `UPPER_SNAKE`.
- Pydantic request/response models live in `schemas/` and are suffixed `Request`/`Response`.
- SQLAlchemy ORM models live in `models/` and are named after their table (`Tenant`, `Investigation`).
- Type aliases for IDs: `TenantId = NewType("TenantId", UUID)`, etc.

### Errors

- All custom exceptions inherit from `ai_sre.exceptions.AISREError`.
- HTTP errors are raised as `HTTPException` in the `api/` layer only. `core/` raises domain exceptions; the API layer translates them.

### Database

- Every new table or column requires an Alembic migration.
- Every query goes through a repository that inherits `TenantScopedRepository` — no exceptions.
- No raw SQL strings; use SQLAlchemy 2 typed select syntax.

### Logging

- Use `structlog`; don't use `print` or the stdlib `logging` module directly.
- Always bind `tenant_id` and (when in an investigation) `investigation_id` to the logger.
- Levels: `DEBUG` for verbose internal state, `INFO` for normal operation, `WARNING` for recoverable, `ERROR` for unhandled.

### Tests

- Every public function in `core/` has a unit test.
- Every API route has at least one happy-path integration test.
- LLM-touching code is tested against a `FakeLLMProvider` in `tests/fakes/`. The provider returns scripted responses.
- Tests must not hit the network. Real Prometheus / Slack are mocked.

### Commits

- One logical change per commit. Tests with code.
- Commit messages: `<area>: <imperative summary>` (e.g. `connectors/prometheus: add bearer auth support`).

---

## Tooling

```bash
make install      # uv-managed venv with all deps
make dev          # FastAPI on :8000, worker process, Postgres in docker
make test         # unit + integration
make lint         # ruff + mypy
make migrate      # alembic upgrade head
make migrate-new MSG="..."  # generate a new migration
```

---

## How an agent should pick up a spec

When asked to "implement spec NNNN":

1. Open `specs/NNNN-*.md`.
2. Open every file listed in **Files to touch**.
3. Open the README of every module those files live in.
4. Open any other doc referenced in the spec's **Context** section.
5. Sketch the change at a high level (1–3 sentences). If anything is ambiguous, surface it before coding.
6. Implement, including tests.
7. Run lint, type-check, tests mentally.
8. Produce a summary listing: what changed, why, what tests cover it, any follow-ups.

---

## Out of scope for an agent without explicit instruction

- Adding or removing third-party dependencies.
- Changing CI / Docker / infrastructure config.
- Changing prompts in `llm/prompts/` (prompt changes have versioning rules — see `llm/prompts/README.md`).
- Schema changes to existing tables without a migration story (data backfill, rollout plan).

---

## When in doubt

Bias toward small, well-tested changes. The system is designed so that each module can evolve independently — use that. Don't refactor across module boundaries unless the spec explicitly calls for it.
