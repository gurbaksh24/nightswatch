# Spec 0010: Slack delivery + OAuth (one-way)

> After Hypothesis runs, post a Slack message. No buttons yet — that's spec 0013.

**Spec ID:** 0010
**Status:** ready-for-agent
**Depends on:** 0002, 0007 (orchestrator can call delivery)

---

## Motivation

FR-8.1, FR-8.2 (Slack message shape); FR-2.3 (Slack OAuth).

## Scope

- [ ] `DeliveryChannel` ABC, `DeliveryReceipt` record (already stubbed in `delivery/base.py`).
- [ ] `DeliveryDispatcher` that looks up the channel for a tenant and calls `deliver()`.
- [ ] `SlackDelivery`:
  - Slack Web API client (use `slack-sdk` async client).
  - Block Kit formatter (header + headline + collapsed alert + hypotheses + action stubs).
  - `deliver()` returns `DeliveryReceipt` with `external_id = message_ts`.
- [ ] Slack OAuth flow:
  - `GET /v1/integrations/slack/oauth/start` — redirect to Slack with state.
  - `GET /v1/integrations/slack/oauth/callback` — exchange code, persist tokens encrypted, create `integration` row with kind=`slack`, public config has `team_id`, `team_name`, `channel_id`, `channel_name`.
- [ ] Action buttons present in the message but inert in MVP — they're activated by spec 0013.
- [ ] Wire the orchestrator to call `DeliveryDispatcher.dispatch(investigation, report)` at end of `run()`.
- [ ] Tests.

## Out of scope

- Interactive callbacks (spec 0013).
- Threaded updates on subsequent investigations (FR-8.3) — single message per investigation in MVP.
- PagerDuty / Email delivery channels.

## Context

- `docs/01-requirements.md` §3.8
- `docs/03-lld.md` §9, §14
- `src/ai_sre/delivery/*` (existing stubs)
- Spec 0002 (envelope encryption for tokens).

## Design

### Files to touch

- `src/ai_sre/delivery/base.py` — finalise types.
- `src/ai_sre/delivery/dispatcher.py` — new.
- `src/ai_sre/delivery/slack/__init__.py` — new package.
- `src/ai_sre/delivery/slack/delivery.py` — implement `SlackDelivery`.
- `src/ai_sre/delivery/slack/block_kit.py` — formatter.
- `src/ai_sre/api/integrations.py` — Slack OAuth start + callback.
- `src/ai_sre/core/investigation/orchestrator.py` — call dispatcher at end of `run()`.
- `tests/unit/delivery/test_block_kit.py` — new.
- `tests/integration/test_slack_oauth.py` — new (mocks slack).

### New / changed contracts

```python
# delivery/base.py
class DeliveryChannel(ABC):
    kind: ClassVar[DeliveryKind]

    @abstractmethod
    async def deliver(self, report: Report, config: dict) -> DeliveryReceipt: ...

    @abstractmethod
    async def handle_callback(self, payload: dict) -> CallbackResult: ...

# delivery/dispatcher.py
class DeliveryDispatcher:
    def __init__(self, channels: dict[DeliveryKind, DeliveryChannel], repo: IntegrationRepository): ...
    async def dispatch(self, tenant_id: UUID, investigation: Investigation, report: Report) -> list[DeliveryReceipt]: ...
```

### Edge cases

- No Slack integration connected → log + return empty receipts; do not raise.
- Slack returns `channel_not_found` (channel deleted) → mark integration `unhealthy`; report has `delivered_at=null`.
- Message size > Slack limits → truncate evidence sections, link to dashboard for full view.
- OAuth state mismatch → 400.
- Report has fewer than 3 hypotheses → render whatever is present (the formatter is robust to 0..N).
- Bot not in channel → Slack returns `not_in_channel`; receipt records error, integration stays healthy.

## Tests

- Unit: block_kit produces valid Block Kit JSON for 0, 1, 3 hypotheses + with/without next_actions.
- Unit: OAuth callback exchanges code, persists integration with team_id, sets channel_id.
- Integration: full orchestrator run posts to mocked Slack and persists `delivery_receipts` on the report.

## Rollout

- Migration: none.
- Env vars: `AI_SRE_SLACK_CLIENT_ID`, `AI_SRE_SLACK_CLIENT_SECRET`, `AI_SRE_SLACK_SIGNING_SECRET`.
- Slack app manifest (separate file) included in `docs/slack-manifest.yaml` — required scopes: `chat:write`, `channels:read`.

## Definition of done

- [ ] Scope complete.
- [ ] OAuth flow connects a test workspace and a follow-on investigation appears in the configured channel.

## Follow-ups

- Spec 0013: callbacks for the buttons.
- FR-8.3: thread updates (post into the original thread on revision).
