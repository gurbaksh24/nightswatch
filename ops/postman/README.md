# Postman collection — E2E flow

`nightswatch.postman_collection.json` walks the whole product loop against a
deployed instance: admin bootstrap → tenant + first API key (spec 0018) →
Prometheus integration → subject service → HMAC-signed Alertmanager webhook →
investigation / report / trace reads → feedback → knowledge upload →
backtest + replay.

## Use

1. Import the collection into Postman.
2. Set two collection variables: `base_url` (defaults to the Fly URL) and
   `admin_token` (the `AI_SRE_ADMIN_TOKEN` you deployed with). Optionally
   `prometheus_url` so selector validation and live queries succeed.
3. Run the folders top to bottom. Test scripts capture every id/key/secret
   into collection variables (`api_key`, `tenant_id`, `webhook_secret`,
   `service_id`, `investigation_id`, ...), so no copy-pasting between steps.

Notables:

- **"4 · Fire an alert"** signs the request body in a pre-request script —
  `X-AI-SRE-Signature: sha256=<HMAC-SHA256(body, webhook_secret)>` — exactly
  what a real Alertmanager forwarder must send. Re-send within 15 minutes to
  see dedupe link the alert to the same investigation.
- **Slack OAuth** can't run inside Postman (browser redirect flow): open
  `{base_url}/v1/integrations/slack/oauth/start` with a Bearer key instead.
- The knowledge upload is multipart — pick a local file before sending.

Keep the collection in sync with `docs/05-api-spec.md` when routes change.
