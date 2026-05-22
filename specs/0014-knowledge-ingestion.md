# Spec 0014: Knowledge ingestion + chunking + embedding

> Tenants upload runbooks and postmortems. We chunk, embed (pgvector), and index per-tenant.

**Spec ID:** 0014
**Status:** ready-for-agent
**Depends on:** 0001

---

## Motivation

FR-7.1–7.3.

## Scope

- [ ] `KnowledgeService.ingest`, `search`, `ingest_past_investigation`.
- [ ] `Chunker` (heading-aware Markdown via `commonmark` or `mistune`; fixed-token fallback for PDF + plain text using `tiktoken`).
- [ ] `EmbeddingProvider` ABC. `BGESmallEmbedder` default impl using `sentence-transformers` in-process.
- [ ] Routes: `POST /v1/knowledge/docs` (multipart), `GET /v1/knowledge/docs`, `GET /v1/knowledge/docs/{id}`, `DELETE /v1/knowledge/docs/{id}`.
- [ ] HNSW index on `knowledge_chunk.embedding` per tenant filter.
- [ ] Tests.

## Out of scope

- Hosted embedding APIs (the ABC supports them; only BGE is wired).
- PDF OCR.
- Tag-based filtering.

## Context

- `docs/01-requirements.md` §3.7
- `docs/03-lld.md` §13
- `docs/04-data-model.md` — `knowledge_doc`, `knowledge_chunk`

## Design

### Files to touch

- `src/ai_sre/core/knowledge/__init__.py`
- `src/ai_sre/core/knowledge/service.py` — implement.
- `src/ai_sre/core/knowledge/chunker.py` — new.
- `src/ai_sre/core/knowledge/embedding.py` — new (ABC + BGE impl).
- `src/ai_sre/api/knowledge.py` — new.
- `src/ai_sre/main.py` — register the knowledge router.
- `migrations/versions/0014_knowledge_hnsw_index.py` — HNSW index.
- Tests.

### Edge cases

- Doc >10 MB → reject 413.
- Empty document → reject 400.
- Embedding model not downloaded yet → first call blocks while loading; subsequent calls fast. Pre-warm on app startup.
- Search returns < k results → return what we have; don't error.

## Tests

- Unit: chunker with various heading depths.
- Unit: deterministic embedding shape (length, dtype).
- Integration: upload → search returns the chunk; tenant isolation in search.

## Rollout

- Migration: HNSW index.
- Disk: BGE model is ~130MB; ship in the container image or download on first start.
- Observability: `aisre_knowledge_docs_total{tenant_id,kind}`, search latency histogram.

## Definition of done

- [ ] Scope complete.
- [ ] Upload a 5kB runbook; search by a phrase from it returns the chunk top-1.
