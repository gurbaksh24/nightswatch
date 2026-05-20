# `ai_sre.core`

Domain logic. HTTP-agnostic. This is where the *behaviour* of the platform lives.

## Subpackages

| Path | Purpose |
|---|---|
| `tenant/` | Tenant CRUD, API key issuance & verification, tenant context. |
| `alert/` | Alert normalisation, fingerprinting, deduplication. |
| `investigation/` | The pipeline, orchestrator, stages — heart of the system. |
| `knowledge/` | Document ingestion, chunking, vector search. |
| `feedback/` | Feedback collection and aggregation. |

## Rules

- May import from `models/`, `schemas/`, `utils/`.
- May import from `connectors/`, `llm/`, `delivery/` **only via their abstract bases**. Concrete implementations are wired in by the registries at startup.
- MUST NOT import from `api/`.
- Every public method gets a docstring. Internal helpers don't need one but their names should explain.

## Pattern

```
api/  ──►  core/SomeService  ──►  core/SomeRepository  ──►  DB
                       │
                       └──►  ConnectorRegistry.for_tenant(...)  ──►  Connector ABC
                       │
                       └──►  LLMGateway.chat(...)              ──►  LLM Provider ABC
                       │
                       └──►  DeliveryDispatcher.dispatch(...)  ──►  DeliveryChannel ABC
```

## When in doubt

If logic feels like "what does this API endpoint return?", it lives here.
If logic feels like "how do I talk to Prometheus?", it lives in `connectors/`.
If logic feels like "how do I parse the request body?", it lives in `api/`.
