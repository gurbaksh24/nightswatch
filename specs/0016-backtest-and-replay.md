# Spec 0016: Backtest + replay endpoints

> Submit a past alert and see what would happen now. Single best onboarding tool.

**Spec ID:** 0016
**Status:** ready-for-agent
**Depends on:** 0007, 0009, 0010, 0012

---

## Motivation

FR-10.3.

## Scope

- [ ] `POST /v1/investigations/backtest` — accepts an Alertmanager payload + service_id + `dry_run` flag. Creates an investigation with `is_backtest=True`, enqueues it. If `dry_run=True`, delivery is skipped.
- [ ] `POST /v1/investigations/{id}/replay` — duplicates an existing investigation as a new one (`replayed_from=<id>`), enqueues it.
- [ ] Orchestrator respects `is_backtest` (no Slack post when dry_run).
- [ ] Tests.

## Out of scope

- Bulk backtest of many historical alerts at once (a script in `scripts/` is fine for one-off; not an endpoint).

## Design

### Files to touch

- `src/ai_sre/models/investigation.py` — add `is_backtest`, `dry_run`, `replayed_from` columns.
- `src/ai_sre/api/investigations.py` — implement endpoints.
- `src/ai_sre/core/investigation/orchestrator.py` — gate delivery on `dry_run`.
- `migrations/versions/0016_investigation_backtest_columns.py` — new.
- Tests.

### Edge cases

- Backtest payload references an integration that was deleted → fail with a clear error in the report; do not 500.
- Replay of a `failed` investigation → still works; just creates a new run.

## Tests

- Unit: dry_run path skips delivery.
- Integration: backtest endpoint produces a complete investigation record without posting to Slack.

## Rollout

- Migration: required.
- Observability: `aisre_investigations_total{kind}` where kind ∈ live / backtest / replay.

## Definition of done

- [ ] Scope complete.
- [ ] Backtest endpoint with a recorded production payload returns a usable report.
