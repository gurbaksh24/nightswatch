# Specs index

> One spec = one PR = one focused unit of work. Hand a spec to an LLM agent (or pick it up yourself) and follow its Definition of Done.

## Recommended build order

The specs are numbered in build order. Each one's "Depends on" list reflects the real prerequisite graph.

| # | Title | Depends on | What you have when it's done |
|---|---|---|---|
| 0001 | Tenant + API keys | — | Auth spine. Every other route can require a tenant. |
| 0002 | Integrations CRUD + envelope encryption | 0001 | Tenants can register a Prometheus and Slack integration (creds encrypted). |
| 0003 | Prometheus connector + health check | 0001, 0002 | Health check flips the integration to `healthy`. The `Connector` ABC is real. |
| 0004 | Subject service registration | 0001–0003 | A tenant can register their one service. |
| 0005 | Topology + metric catalog discovery | 0003, 0004 | Background refresh fills `service_dependency` and `metric_catalog_entry`. |
| 0006 | Alert webhook + dedupe + enqueue | 0001, 0002, 0004 | Alertmanager → 202 → investigation row + Procrastinate job. |
| 0007 | Investigation orchestrator skeleton + Triage | 0001, 0002, 0004, 0005, 0006 | Alert → investigation runs end-to-end with placeholder stages. |
| 0008 | LLM Gateway + Anthropic + tool registry | 0007 | Real LLM calls with budget + observability. No tools yet. |
| 0009 | `query_prometheus` tool + HypothesisStage | 0003, 0005, 0007, 0008 | First time the model actually queries customer data. |
| 0010 | Slack delivery + OAuth | 0002, 0007 | Investigation results land in Slack. |
| 0011 | ValidationStage | 0008, 0009 | Each hypothesis is validated with targeted queries. |
| 0012 | ReportStage + investigation read endpoints | 0008, 0011 | Structured RCA + dashboard read APIs. |
| 0013 | Slack interactive callbacks + feedback | 0010, 0012 | Buttons work. Feedback persisted. |
| 0014 | Knowledge ingestion + chunking + embedding | 0001 | Tenants upload runbooks. Vector search works. |
| 0015 | `search_runbooks` + `search_past_incidents` tools | 0008, 0009, 0012, 0014 | LLM can pull from runbooks + past investigations. |
| 0016 | Backtest + replay endpoints | 0007, 0009, 0010, 0012 | Submit a past alert and see what the system would have said. |
| 0017 | Hardening — rate limits, observability, budgets | 0006, 0008, 0010 | Production-shaped. |

## First end-to-end alert → Slack RCA

By the end of **0010** you have a working "alert hits webhook → placeholder hypotheses + tool calls → Slack message" loop. That's the demo. 0011–0017 are quality + completeness.

## Working in parallel

Some specs can be developed concurrently if you have more than one agent / engineer:

- **0014 (knowledge ingestion)** is independent — only depends on 0001. Can start anytime.
- **0008 (LLM Gateway)** can be started before 0007 finishes if 0007's interfaces are settled. They wire together in 0009.
- **0010 (Slack delivery)** can be developed in parallel with 0008–0009 — the Slack code doesn't depend on the LLM stages, only on the orchestrator being callable.

## When you find a gap

If you realise a spec is missing something — a column, a field, an edge case — update the spec file in the same PR that adds it. Don't carry a verbal patch in your head.

## When you find a spec is wrong

If a spec disagrees with the docs (`docs/01-requirements.md`, etc.), the docs win. Update the spec to match, or update both if the docs were wrong — but never silently follow the spec into a docs contradiction.

## Spec format

See `TEMPLATE.md`. The headings are mandatory; the content under each heading is whatever the spec author judges sufficient. An agent reading a spec should be able to produce a PR without further questions; if it can't, the spec is incomplete and needs revision.
