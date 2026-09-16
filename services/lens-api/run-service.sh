#!/usr/bin/env bash
# systemd ExecStart wrapper for bst-lens-api (container-free). Rebuilds the DSN against the
# local cluster from the .env POSTGRES_* values (the .env DSN targets the compose host).
set -euo pipefail
export LENS_DB_DSN="postgresql://${POSTGRES_USER:-lens}:${POSTGRES_PASSWORD}@127.0.0.1:5432/${POSTGRES_DB:-lens}"
exec /home/vily/lens-api-local/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 7710
