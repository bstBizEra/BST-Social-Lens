#!/usr/bin/env bash
# Phase 5 live smoke (001B §8): optionally restart bst-lens-api (RESTART=1) and exercise /raw, /ingest, /provenance.
# Secrets are read from .env and never echoed.
set -u
cd "$(dirname "$0")/.." || exit 1
[ "${RESTART:-0}" = 1 ] && sudo systemctl restart bst-lens-api
sleep 4
systemctl is-active bst-lens-api
TOKEN=$(sed -n 's/^LENS_API_TOKEN=//p' .env | tr -d '\r"' | head -1)
[ -n "$TOKEN" ] || { echo "no token in .env"; exit 1; }
H="Authorization: Bearer $TOKEN"
B=${LENS_URL:-http://127.0.0.1:7710}
echo "--- health"; curl -s $B/health; echo
BODY='{"smoke":"phase5","lao":"ດິນ ຂາຍ"}'
HASH=$(printf '%s' "$BODY" | sha256sum | cut -d' ' -f1)
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "--- POST /raw (new)"
curl -s -X POST $B/raw -H "$H" -H 'content-type: application/json' \
  -d "{\"source\":\"smoke\",\"version\":\"0.7.0\",\"captures\":[{\"payload_hash\":\"$HASH\",\"platform\":\"facebook\",\"url\":\"https://example.invalid/smoke\",\"captured_at\":\"$NOW\",\"body\":$(printf '%s' "$BODY" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')}]}"; echo
echo "--- POST /raw (duplicate)"
curl -s -X POST $B/raw -H "$H" -H 'content-type: application/json' \
  -d "{\"source\":\"smoke\",\"version\":\"0.7.0\",\"captures\":[{\"payload_hash\":\"$HASH\",\"platform\":\"facebook\",\"url\":\"https://example.invalid/smoke\",\"captured_at\":\"$NOW\",\"body\":$(printf '%s' "$BODY" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')}]}"; echo
echo "--- POST /raw (bad hash -> rejected)"
curl -s -X POST $B/raw -H "$H" -H 'content-type: application/json' \
  -d "{\"source\":\"smoke\",\"version\":\"0.7.0\",\"captures\":[{\"payload_hash\":\"$(printf 'x%.0s' $(seq 64))\",\"platform\":\"facebook\",\"url\":\"https://example.invalid/bad\",\"captured_at\":\"$NOW\",\"body\":\"{}\"}]}"; echo
echo "--- POST /ingest (record referencing the raw hash)"
curl -s -X POST $B/ingest -H "$H" -H 'content-type: application/json' \
  -d "{\"source\":\"smoke\",\"version\":\"0.7.0\",\"records\":[{\"key\":\"facebook:smoke-phase5-1\",\"platform\":\"facebook\",\"post_id\":\"smoke-phase5-1\",\"record_type\":\"post\",\"text\":\"smoke ດິນ ຂາຍ\",\"captured_at\":\"$NOW\",\"payload_hash\":\"$HASH\",\"content_hash\":\"$HASH\"}]}"; echo
echo "--- GET /provenance/facebook:smoke-phase5-1"
curl -s $B/provenance/facebook:smoke-phase5-1 -H "$H"; echo
echo "--- GET /provenance (coverage)"
curl -s $B/provenance -H "$H"; echo
echo "--- purge no-op check (raw_days=0&days=0 must be a no-op per 001B §8.2)"
curl -s -X POST "$B/admin/purge?raw_days=0&days=0" -H "$H"; echo
echo "--- journal tail"
journalctl -u bst-lens-api -n 8 --no-pager | sed -E 's/(Bearer|token=)[^ ]*/\1***/g'
