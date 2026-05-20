# AI-SRE

An AI-driven Site Reliability Engineering platform. Customers connect their Prometheus + Alertmanager + Slack; we ingest alerts and post a structured Root Cause Analysis to Slack within ~5 minutes.

> **Status:** boilerplate / scaffolding. The structure is in place; most modules ship as documented stubs to be implemented spec-by-spec. See [`AGENTS.md`](AGENTS.md) for the workflow.

---

## Quickstart

```bash
# 1. Install deps (uv-managed venv)
make install

# 2. Bring up Postgres locally
docker-compose up -d postgres

# 3. Run migrations
make migrate

# 4. Start the API + worker
make dev
```

The API serves at `http://localhost:8000`. OpenAPI at `/docs`.

---

## What's here

```
docs/         # requirements, HLD, LLD, data model, API spec, ADRs
specs/        # one Markdown file per unit of work (spec-driven development)
src/ai_sre/   # the Python package
tests/        # unit + integration
migrations/   # Alembic
```

## What's not here (yet)

Most of `src/ai_sre/` is documented stubs. Implementing them is the work, and each piece has (or should have) a spec under `specs/`. See `specs/0001-tenant-and-api-keys.md` for an example of what a spec looks like.

## Working in this repo

Read [`AGENTS.md`](AGENTS.md). It's the single source of truth for conventions, module rules, and the spec-driven workflow.

## Reading order

1. [`docs/01-requirements.md`](docs/01-requirements.md)
2. [`docs/02-hld.md`](docs/02-hld.md)
3. [`docs/03-lld.md`](docs/03-lld.md)
4. [`docs/04-data-model.md`](docs/04-data-model.md)
5. [`docs/05-api-spec.md`](docs/05-api-spec.md)
6. [`AGENTS.md`](AGENTS.md)
7. [`specs/0001-tenant-and-api-keys.md`](specs/0001-tenant-and-api-keys.md) — example spec

## License

TBD.
