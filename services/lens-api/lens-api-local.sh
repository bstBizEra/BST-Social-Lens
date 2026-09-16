#!/usr/bin/env bash
# BST Social Lens — runbook §2a container-free lens-api on bizera-wsl.
# Usage: bash lens-api-local.sh {check|setup|start|stop|status|health|auth}
# Secrets are read from services/lens-api/.env and never printed.
set -euo pipefail
REPO=/mnt/c/laragon/www/BST-Social-Lens
API="$REPO/services/lens-api"
HOME_DIR="$HOME/lens-api-local"
VENV="$HOME_DIR/venv"
LOG="$HOME_DIR/lens-api.log"
PIDF="$HOME_DIR/lens-api.pid"

load_env() {
  set -a; . "$API/.env"; set +a
  : "${POSTGRES_USER:=lens}" "${POSTGRES_DB:=lens}"
  if [ -z "${POSTGRES_PASSWORD:-}" ] || [ -z "${LENS_API_TOKEN:-}" ]; then
    echo "ERROR: POSTGRES_PASSWORD / LENS_API_TOKEN missing in $API/.env" >&2; exit 2
  fi
  export LENS_DB_DSN="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:5432/${POSTGRES_DB}"
  export LENS_CONSOLE_DIR="$REPO/console"
}

case "${1:-status}" in
  check)
    load_env
    echo "user=$(whoami) python=$(python3 --version 2>&1) pg_user=$POSTGRES_USER pg_db=$POSTGRES_DB token_len=${#LENS_API_TOKEN} pw_len=${#POSTGRES_PASSWORD}"
    echo "role exists: $(sudo -u postgres psql -tAc "select count(*) from pg_roles where rolname='${POSTGRES_USER}'")"
    echo "db exists:   $(sudo -u postgres psql -tAc "select count(*) from pg_database where datname='${POSTGRES_DB}'")"
    pg_lsclusters 2>/dev/null || true
    ;;
  setup)
    load_env
    mkdir -p "$HOME_DIR"
    if [ "$(sudo -u postgres psql -tAc "select count(*) from pg_roles where rolname='${POSTGRES_USER}'")" = "0" ]; then
      sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE USER ${POSTGRES_USER} WITH PASSWORD '${POSTGRES_PASSWORD}';"
      echo "created role ${POSTGRES_USER}"
    else
      sudo -u postgres psql -v ON_ERROR_STOP=1 -c "ALTER USER ${POSTGRES_USER} WITH PASSWORD '${POSTGRES_PASSWORD}';"
      echo "role ${POSTGRES_USER} exists — password synced to .env"
    fi
    if [ "$(sudo -u postgres psql -tAc "select count(*) from pg_database where datname='${POSTGRES_DB}'")" = "0" ]; then
      sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER};"
      echo "created db ${POSTGRES_DB}"
    fi
    sudo -u postgres psql -v ON_ERROR_STOP=1 -d "${POSTGRES_DB}" -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;" >/dev/null
    [ -d "$VENV" ] || python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q -r "$API/requirements.txt"
    echo "venv ready: $("$VENV/bin/python" -c 'import fastapi,uvicorn,asyncpg;print("fastapi",fastapi.__version__)')"
    ;;
  start)
    load_env
    if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then echo "already running pid $(cat "$PIDF")"; exit 0; fi
    mkdir -p "$HOME_DIR"
    cd "$API"
    nohup "$VENV/bin/uvicorn" app.main:app --host 127.0.0.1 --port 7710 >"$LOG" 2>&1 &
    echo $! >"$PIDF"
    sleep 3
    echo "started pid $(cat "$PIDF"); log $LOG"
    tail -n 5 "$LOG"
    ;;
  stop)
    if [ -f "$PIDF" ]; then kill "$(cat "$PIDF")" 2>/dev/null && echo "stopped $(cat "$PIDF")"; rm -f "$PIDF"; else echo "not running"; fi
    ;;
  status)
    if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then echo "running pid $(cat "$PIDF")"; else echo "not running"; fi
    ss -ltn 2>/dev/null | grep -E ':7710' || true
    ;;
  health)
    curl -s http://127.0.0.1:7710/health; echo
    curl -s -o /dev/null -w "console HTTP %{http_code}\n" http://127.0.0.1:7710/
    ;;
  auth)
    load_env
    curl -s -o /dev/null -w "stats without token: HTTP %{http_code}\n" http://127.0.0.1:7710/stats
    curl -s -H "Authorization: Bearer ${LENS_API_TOKEN}" http://127.0.0.1:7710/stats; echo
    curl -s -H "Authorization: Bearer ${LENS_API_TOKEN}" "http://127.0.0.1:7710/seen?limit=3"; echo
    ;;
  *) echo "usage: $0 {check|setup|start|stop|status|health|auth}"; exit 1 ;;
esac
