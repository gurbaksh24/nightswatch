# `ai_sre.delivery`

Output channels for investigation reports.

## MVP

Slack only.

## Adding a channel (post-MVP)

Per **NFR-8.2** (extensibility):

1. New file `<channel>.py` implementing `DeliveryChannel`.
2. Register in the dispatcher's switch / registry.
3. Document message format here.

## Slack message format

See LLD §14 for the full Block Kit layout. Quick summary:

```
🚨 [Alert name] — [confidence badge]

[Headline diagnosis, 1–2 sentences]

▼ Triggering alert (collapsed)

Hypothesis 1: [statement]                            [confidence]
  Evidence: [bullet] [bullet] [bullet]
Hypothesis 2: ...
Hypothesis 3: ...

Suggested next actions:
- [action 1]
- [action 2]

[Full investigation →]   [Past similar →]

👍 useful   👎 not useful   🎯 actual cause   View trace
```

## Callbacks

Interactive button clicks in Slack hit `POST /v1/delivery/slack/callback` —
**not** an authenticated API endpoint. Verification: Slack's request signing
scheme (HMAC of `v0:{timestamp}:{body}`).

The callback routes to `SlackDelivery.handle_callback`, which translates the
Slack payload into a `FeedbackEvent` and persists it.
