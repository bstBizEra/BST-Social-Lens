#!/usr/bin/env bash
# Smoke-test the /mcp adapter against the live lens-api on bizera-wsl. Token from .env, never printed.
set -euo pipefail
set -a; . /mnt/c/laragon/www/BST-Social-Lens/services/lens-api/.env; set +a
U=http://127.0.0.1:7710/mcp
H=(-H "Authorization: Bearer ${LENS_API_TOKEN}" -H "content-type: application/json")
echo "-- no token:"; curl -s -o /dev/null -w "%{http_code}\n" -H "content-type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' $U
echo "-- initialize:"; curl -s "${H[@]}" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' $U; echo
echo "-- tools/list names:"; curl -s "${H[@]}" -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' $U | python3 -c 'import sys,json;print([t["name"] for t in json.load(sys.stdin)["result"]["tools"]])'
echo "-- get_stats:"; curl -s "${H[@]}" -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_stats","arguments":{}}}' $U; echo
echo "-- search_records (SQL path):"; curl -s "${H[@]}" -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"search_records","arguments":{"query":"ດິນ","since":"2026-01-01T00:00:00Z","keyword":"ດິນ","limit":5}}}' $U; echo
echo "-- top_containers:"; curl -s "${H[@]}" -d '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"top_containers","arguments":{"platform":"facebook"}}}' $U; echo
echo "-- get_record missing:"; curl -s "${H[@]}" -d '{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"get_record","arguments":{"key":"facebook:0"}}}' $U; echo
echo "-- list_seen:"; curl -s "${H[@]}" -d '{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"list_seen","arguments":{"status":"seen"}}}' $U; echo
