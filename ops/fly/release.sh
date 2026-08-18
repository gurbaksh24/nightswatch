#!/bin/sh
# Fly release command: runs on a throwaway machine before each deploy is
# promoted. Two schema owners, two steps (see docs/02-hld.md §4):
#   1. Alembic owns the domain tables.
#   2. Procrastinate owns its queue tables (procrastinate_jobs, ...).
set -e

echo "==> alembic upgrade head"
alembic upgrade head

echo "==> procrastinate schema --apply"
procrastinate -a ai_sre.workers.app.procrastinate_app schema --apply

echo "==> release OK"
