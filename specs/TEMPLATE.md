# Spec: <short title>

> One-paragraph version of the change. What is this PR? Why now?

**Spec ID:** NNNN
**Status:** draft | ready-for-agent | in-progress | done
**Author:** <name>
**Created:** YYYY-MM-DD

---

## Motivation

Why is this change needed? Reference a requirement (`FR-x.y`, `NFR-x.y`) or a user/customer signal where possible. Keep this to a few sentences — if it needs more, write a follow-up doc and link it.

---

## Scope

A bullet list of what's in scope. Be specific.

- [ ] Add X.
- [ ] Modify Y to support Z.
- [ ] Write tests for the above.

---

## Out of scope

What this spec is **not** doing. The agent will not touch these.

- Anything not in the Scope list.
- (List specific tempting-but-deferred items here.)

---

## Context

Files and docs the agent must read before starting. Don't be stingy — too much context is fine; too little leads to confused code.

- `docs/01-requirements.md` §X.Y
- `docs/03-lld.md` §N
- `src/ai_sre/path/to/file.py`
- (etc.)

---

## Design

The concrete change. Be specific enough that a competent engineer (or a competent LLM) can implement it without further questions.

### Files to touch

- `src/ai_sre/...` — what changes here, briefly.
- `tests/...` — new tests.
- `migrations/versions/<rev>_<desc>.py` — if schema changes.

### New / changed contracts

```python
# Paste the new function signatures, class definitions, schemas, etc.
# Be precise about types.
```

### Data model changes

If any. Include the migration SQL or Alembic operations.

### Edge cases

- What if the LLM returns malformed output?
- What if the tenant has no integrations connected?
- What if a tool call times out?
- (etc.)

---

## Tests

What's the minimum set of tests that proves this works?

- Unit: <list>
- Integration: <list>
- (Manual smoke if applicable.)

---

## Rollout

- Migrations required? Y / N
- Backward compatible? Y / N
- Feature flag? <name or "none">
- Observability: any new metrics or log fields?

---

## Definition of done

A checklist the agent (or reviewer) signs off on:

- [ ] All "Scope" items implemented.
- [ ] Tests written and passing.
- [ ] `make lint` clean.
- [ ] `make test` green.
- [ ] Docs updated if behaviour changed (FR/NFR, HLD, LLD).
- [ ] Migration runs cleanly on a fresh DB and on a production-shape DB.
- [ ] No new dependencies added (or new ones explicitly listed and justified).

---

## Follow-ups

Things deliberately deferred. Each one is a candidate for the next spec.

- (Empty unless you've thought of something.)
