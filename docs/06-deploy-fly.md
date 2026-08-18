# Deploying to Fly.io

The cheapest workable production shape (~$9/mo): one Fly app for the platform
(two process groups → API machine + worker machine) and a second Fly app
running self-managed Postgres+pgvector on a volume. Managed Postgres is
deliberately avoided (from $38/mo — triples the bill at MVP scale).

```
nightswatch (Fly app)                nightswatch-db (Fly app)
├── api    machine  ── /readyz ✓     └── pgvector/pgvector:pg16
└── worker machine                       + 3GB volume (pgdata)
        │  private 6PN network               ▲
        └────────────────────────────────────┘
             nightswatch-db.internal:5432
```

Prereqs: `flyctl` installed, `fly auth login` done, billing set up.
App names are globally unique on Fly — if `nightswatch` is taken, pick
another and change `app = ...` in both fly.toml files.

## 1. Postgres

Generate the DB password into a variable and **save it in your password
manager before setting it** — Fly can never show a secret back, and you need
this value again in `AI_SRE_DB_URL`:

```bash
DB_PASSWORD="$(openssl rand -base64 24)"
printf 'POSTGRES_PASSWORD: %s\n' "$DB_PASSWORD"   # save this now
```

```bash
fly apps create nightswatch-db
fly volumes create pgdata --app nightswatch-db --region iad --size 3 --yes
fly secrets set --app nightswatch-db POSTGRES_PASSWORD="$DB_PASSWORD"
fly deploy --config ops/fly/db/fly.toml
```

If you ever lose the password *after* the first deploy, changing the secret
is not enough (Postgres bakes it in at initdb) — with no data yet, destroy
the machine + volume and redeploy with a fresh one.

## 2. Platform app + secrets

Generate the two app-owned secrets into variables and **save both in your
password manager first** (the admin token is needed for every tenant-creation
call later; Fly cannot display secrets after they're set):

```bash
ADMIN_TOKEN="$(openssl rand -base64 24)"
ENCRYPTION_KEY="$(python3 -c 'import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')"
printf 'AI_SRE_ADMIN_TOKEN: %s\nAI_SRE_TENANT_ENCRYPTION_KEY: %s\n' "$ADMIN_TOKEN" "$ENCRYPTION_KEY"   # save both now
```

```bash
fly apps create nightswatch

fly secrets set --app nightswatch \
  AI_SRE_DB_URL="postgresql+asyncpg://aisre:$DB_PASSWORD@nightswatch-db.internal:5432/aisre" \
  AI_SRE_ADMIN_TOKEN="$ADMIN_TOKEN" \
  AI_SRE_TENANT_ENCRYPTION_KEY="$ENCRYPTION_KEY" \
  AI_SRE_LLM_API_KEY="<anthropic api key>" \
  AI_SRE_LLM_MODEL="<verify a live model id for your account>" \
  AI_SRE_SLACK_CLIENT_ID="<slack app client id>" \
  AI_SRE_SLACK_CLIENT_SECRET="<slack app client secret>" \
  AI_SRE_SLACK_SIGNING_SECRET="<slack app signing secret>"
```

Every value above overrides an insecure or empty default — the app is not
production-safe without all of them. `AI_SRE_TENANT_ENCRYPTION_KEY` must
decode to exactly 32 bytes or crypto raises at first use. Without
`AI_SRE_LLM_API_KEY` the pipeline still runs but produces placeholder RCAs.

## 3. Deploy

```bash
fly deploy
```

The release command applies both schemas (Alembic domain tables +
Procrastinate queue tables) before promotion; the API health check gates on
`/readyz`, which verifies both. Confirm one machine per process group:

```bash
fly scale count api=1 worker=1
fly status
```

## 4. Bootstrap a tenant

```bash
BASE="https://nightswatch.fly.dev"
ADMIN_TOKEN="<the AI_SRE_ADMIN_TOKEN you set>"

# tenant + API key
curl -s -X POST "$BASE/v1/tenant" -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" -d '{"name": "Acme", "slug": "acme"}'
curl -s -X POST "$BASE/v1/auth/api-keys" -H "Authorization: Bearer <tenant flow>" ...
```

Then follow the normal onboarding order (docs/05-api-spec.md): create the
Prometheus integration (returns the one-time webhook signing secret), register
the subject service, connect Slack via `/v1/integrations/slack/oauth/start`,
and point the customer's Alertmanager at
`$BASE/v1/webhooks/alertmanager/{tenant_id}` with the HMAC secret.

Slack app config must list these URLs:
- OAuth redirect: `$BASE/v1/integrations/slack/oauth/callback`
- Interactivity request URL: `$BASE/v1/delivery/slack/callback`

## Operational notes

- **Don't enable auto-stop on the API.** `fly.toml` pins
  `auto_stop_machines = "off"` — an idled machine drops customer webhooks.
- **`/metrics` is unauthenticated** and exposed on the public app URL, with
  tenant ids in labels. Acceptable for a first deploy; before onboarding real
  customers either scrape over the private network (Flycast) or put a token
  check in front of it.
- **Backups are yours.** Fly volume snapshots are short-retention. Add a
  scheduled `pg_dump` shipped off-Fly (a Fly machine cron or GitHub Actions
  hitting the DB over WireGuard) before storing real customer data.
- **Rate limiter is per-instance.** Fine at api=1; if you scale the API out,
  each machine enforces its own window.
- **Scaling down cost:** if 512MB proves roomy (`fly machine status` shows
  memory), `fly scale memory 256 --process-group api` etc. drops the bill to
  ~$6.5/mo.
