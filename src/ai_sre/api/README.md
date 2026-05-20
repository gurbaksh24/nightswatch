# `ai_sre.api`

HTTP route handlers. **Thin** — these files should mostly:

1. Parse the request body (Pydantic).
2. Resolve the calling tenant via `deps.current_tenant`.
3. Call a `core/` service.
4. Map the service result to a response model.

## Rules

- No business logic here. If a function body grows past ~30 lines, the logic belongs in `core/`.
- No direct DB access here. Repositories live in `core/`.
- No catch-all `except Exception`. Let domain exceptions propagate; the global handler translates them.
- Every endpoint has its types defined in `schemas/` — never inline `dict` types.

## Files

| File | Purpose |
|---|---|
| `deps.py` | FastAPI dependencies: DB session, current tenant, admin auth. |
| `tenants.py` | Tenant CRUD, profile. |
| `integrations.py` | Integration CRUD + health checks + Slack OAuth callback. |
| `services.py` | Subject service register/get + topology + metric catalog. |
| `alerts.py` | Alertmanager webhook ingress. |
| `investigations.py` | Investigation list/detail/replay/backtest. |
| `feedback.py` | Feedback events. |

## Dependency direction

```
api/ ──► core/, schemas/, models/, utils/
```
Never the reverse.

## See also

- `docs/05-api-spec.md`
