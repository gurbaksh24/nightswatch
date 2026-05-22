# Spec 0013: Slack interactive callbacks + feedback

> Activate the 👍 / 👎 / 🎯 buttons. Persist feedback. Expose via API.

**Spec ID:** 0013
**Status:** ready-for-agent
**Depends on:** 0010, 0012

---

## Motivation

FR-9.1–9.3, FR-8.2 (button rendering already in 0010 but inert).

## Scope

- [ ] `POST /v1/delivery/slack/callback` endpoint with Slack request signing verification.
- [ ] `SlackDelivery.handle_callback`:
  - Parse button payload → produce a `CallbackResult` with `kind="feedback"`, investigation_id + button kind.
  - For "🎯 mark actual cause": open a Slack modal for free text; handle the modal submission.
- [ ] `FeedbackService.record(...)` writes to the `feedback` table.
- [ ] `POST /v1/investigations/{id}/feedback`, `GET /v1/investigations/{id}/feedback`.
- [ ] Tests.

## Out of scope

- ML signal pipeline from feedback to eval set (later).
- Editing / deleting feedback.

## Context

- `docs/01-requirements.md` §3.9
- `docs/03-lld.md` §14 (Slack callbacks)

## Design

### Files to touch

- `src/ai_sre/api/feedback.py` — implement.
- `src/ai_sre/api/delivery.py` — new (the Slack callback endpoint).
- `src/ai_sre/delivery/slack/delivery.py` — implement `handle_callback`.
- `src/ai_sre/delivery/slack/modals.py` — new (modal payload builders).
- `src/ai_sre/core/feedback/service.py` — implement.
- Tests.

### Edge cases

- Slack request signature stale (>5 min) → reject 401.
- Same user clicks 👍 then 👎 → both rows are stored; UI shows last-write-wins per actor.
- Modal submitted but investigation has been deleted → return error to Slack; don't 500.

## Tests

- Unit: signature verification across valid/invalid/stale.
- Unit: button payload → feedback kind mapping.
- Integration: click → POST → row persisted → GET returns it.

## Rollout

- Slack app manifest needs interactivity URL + scope `commands` (if using slash commands later, not now).
- Observability: `aisre_feedback_total{kind}`.

## Definition of done

- [ ] Scope complete.
- [ ] Clicking 👍 / 👎 from a delivered Slack message persists a row and is visible via GET.
