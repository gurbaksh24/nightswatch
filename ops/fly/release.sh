#!/bin/sh
# Fly release command: runs on a throwaway machine before each deploy is
# promoted. Two schema owners, two steps (see docs/02-hld.md §4):
#   1. Alembic owns the domain tables (idempotent: upgrade to head).
#   2. Procrastinate owns its queue tables (procrastinate_jobs, ...).
set -e

echo "==> alembic upgrade head"
alembic upgrade head

# `schema --apply` runs the baseline SQL and is NOT idempotent — it errors on
# a DB that already has the schema ("type procrastinate_job_status already
# exists"). `healthchecks` exits non-zero exactly when the procrastinate
# schema is missing, so it gates the apply. (A procrastinate version upgrade
# that needs incremental migrations is a manual step — see
# `procrastinate schema --migrations-path`.)
if procrastinate -a ai_sre.workers.app.procrastinate_app healthchecks >/dev/null 2>&1; then
  echo "==> procrastinate schema already applied — skipping"
else
  echo "==> procrastinate schema --apply"
  procrastinate -a ai_sre.workers.app.procrastinate_app schema --apply
fi

echo "==> release OK"
