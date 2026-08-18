# Spec: Runbook auto-ingestion from alert annotations

> When an alert carries a `runbook_url` annotation and that URL hasn't been
> ingested yet, fetch it and index it into the knowledge base before the
> investigation's LLM stages run — so `search_runbooks` cites the exact
> runbook for the monitor that fired, written by the team that owns it.

**Spec ID:** 0021
**Status:** ready-for-agent
**Author:** Gurbaksh Singh Gabbi (+ Claude)
**Created:** 2026-08-18

---

## Motivation

FR-7.x leverage: the reference client's `monitors.yaml` declares a
`runbook_url` per monitor, which spec 0020 already carries into alert
annotations. Alertmanager tenants use the same `runbook_url` annotation
convention, so this works for both ingress paths. It is the highest-leverage
RCA-quality improvement available: the model gets the human-authored,
monitor-specific runbook instead of whatever was manually uploaded.

---

## Scope

- [ ] `core/knowledge/runbook_fetcher.py` — fetch + extract text
      (markdown/plain, HTML→text via stdlib, PDF via pypdf) with hard
      safety rails (below).
- [ ] `KnowledgeService.ingest_runbook_from_url(url, ...)` — dedupe by URL
      via the (previously unused) `knowledge_doc.source_object_key` column;
      outcomes: `ingested` / `already_ingested`.
- [ ] `KnowledgeRepository.find_doc_by_source_key`.
- [ ] Orchestrator hook: after context build, before the pipeline — fetch is
      best-effort and time-bounded; failure never delays or fails the
      investigation beyond the timeout.
- [ ] Settings: `runbook_fetch_enabled` (default true),
      `runbook_fetch_timeout_seconds` (default 10).
- [ ] Tests.

## Out of scope

- Authenticated runbook sources (Confluence/Notion tokens) — public URLs
  only in MVP; failures are logged and skipped.
- Re-fetching / staleness TTL (first ingest wins; refresh is a follow-up).
- Bulk ingestion from the tenant's git repo.

## Context

- `src/ai_sre/core/knowledge/{service,repository,chunker}.py`
- `src/ai_sre/core/investigation/orchestrator.py` (hook point;
  `_ingest_past_investigation` is the best-effort pattern to mirror)
- `src/ai_sre/api/knowledge.py` (PDF extraction pattern)
- spec 0020 (where `runbook_url` lands in annotations)

## Design

### Safety rails (the URL arrives from a webhook → SSRF surface)

- `https://` only.
- Hostname resolved (all A/AAAA records) and rejected if any address is
  private, loopback, link-local, reserved, multicast, or unspecified.
  (DNS-rebinding TOCTOU is acknowledged and out of MVP scope.)
- Redirects are NOT followed (a redirect could bounce to an internal host).
- Response capped at `knowledge_max_upload_bytes`; request capped at
  `runbook_fetch_timeout_seconds`; content-type allowlist
  (text/*, application/pdf).
- The whole hook wrapped in `asyncio.wait_for` — a hung fetch costs at most
  the timeout, never the investigation.

### Flow

```
orchestrator.run → _build_context → [runbook hook] → pipeline stages
                                        │
                     alert.annotations.runbook_url present?
                     already ingested (source_object_key == url)? → skip
                     fetch (guarded) → chunk → embed → knowledge_doc
                                                       kind="runbook"
```

The doc's `source_object_key` is the URL — dedupe key across
investigations; `extra_metadata` records `{"source_url", "auto_ingested"}`.

### Edge cases

- No knowledge service wired (no embedder) → skip silently.
- `runbook_fetch_enabled=false` → skip.
- Fetch/guard/parse failure → `orchestrator.runbook_ingest_failed` warning,
  investigation proceeds.
- Two concurrent investigations racing the same URL: both may ingest
  (duplicate doc, harmless for search); acceptable MVP trade-off,
  documented.

## Tests

- Unit: URL guards (http, localhost, private/loopback literals + resolver
  injection), content extraction (markdown passthrough, HTML→text, oversize,
  non-200, timeout), orchestrator hook (calls service when annotation
  present, skips when absent/disabled).
- Integration: `ingest_runbook_from_url` creates the doc with
  `source_object_key`, chunks + embeds it, `search(kinds=["runbook"])`
  finds it; second call returns `already_ingested` without a duplicate.

## Rollout

- Migrations required? N (`source_object_key` exists since 0011).
- Backward compatible? Y (flag-gated, best-effort).
- Feature flag? `AI_SRE_RUNBOOK_FETCH_ENABLED`.
- Observability: `orchestrator.runbook_ingest` / `_failed` structured logs.

## Definition of done

- [ ] Scope complete; tests passing; lint + mypy clean; no new deps.

## Follow-ups

- Refresh TTL for stale runbooks; authenticated sources; bulk git-repo
  ingestion.
