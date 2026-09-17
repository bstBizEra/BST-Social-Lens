# lens-api + lens-db

The server tier of BST Social Lens: a stateless FastAPI **ingest API** in front of a **PostgreSQL** system of record (LensDB). Runs on `bizera-wsl` under Podman. See `docs/03-architecture/adr-0001-lensdb-ingest-topology.md` for the why.

## Topology

```
Edge/Chrome extension ──POST /ingest──▶ lens-api (:7710) ──▶ lens-db (Postgres, pod network only)
Console artifact ──────GET /records───▶ lens-api (:7710) ◀────────┘
```

- `lens-api` is published on `127.0.0.1:7710` only. WSL2 forwards Windows `localhost:7710` to it, which is the extension's default ingest URL.
- `lens-db` has **no published ports** — reachable only on the Podman pod network. One writer, one reader, both `lens-api`.

## Run (podman-compose or docker compose)

```bash
cd services/lens-api
cp .env.example .env          # set LENS_API_TOKEN to a long random string
podman compose up -d --build  # or: docker compose up -d --build
curl -s http://localhost:7710/health
```

Point the extension side panel's **Ingest URL** at `http://localhost:7710/ingest` and paste the same `LENS_API_TOKEN` into **Bearer token**.

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | none | liveness + record count |
| POST | `/ingest` | bearer | upsert a batch of `SocialRecord`s |
| GET | `/records?platform=&container_id=&limit=&offset=` | bearer | paginated read for the Console |
| GET | `/stats` | bearer | per-platform counts + last capture |

`/ingest` body matches what the extension sends:

```json
{ "source": "bst-social-lens", "version": "0.2.0", "records": [ /* SocialRecord[] */ ] }
```

Response: `{ "received": n, "inserted": i, "updated": u, "run_id": id }`.

## Dedup semantics

Primary key is `key = "${platform}:${post_id}"`. On conflict the row is updated, not duplicated: engagement counts and text refresh via `COALESCE`, `captured_at` takes `GREATEST`, `created_at` and `first_seen` are preserved. This makes repeated syncs from multiple capture accounts idempotent.

## Auth

Bearer token via `LENS_API_TOKEN`. If unset, auth is **disabled** — dev only; always set it in `.env`. When the Console and BST agents both consume, migrate to short-lived tokens issued by MCP Hub (Phase 4).

## Tests

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q          # contract + dedup, no live DB (FakeDB)
```

Endpoint, auth, validation and `record_to_row` flattening are covered without Postgres. The SQL upsert itself is validated in CI against a real Postgres (see the repo's engineering docs); locally you can point `LENS_DB_DSN` at any Postgres 16 and run the app.

## Config

| Env | Default | Meaning |
|---|---|---|
| `LENS_DB_DSN` | `postgresql://lens:lens@lens-db:5432/lens` | Postgres DSN |
| `LENS_API_TOKEN` | *(empty = auth off)* | bearer token |
| `LENS_CORS_ORIGINS` | *(empty)* | comma-separated browser origins allowed to call the API (Console artifact URL) |

## Container-free run (runbook §2a)

`lens-api-local.sh {check|setup|start|stop|status|health|auth}` — one-off run under `uvicorn` against a local PostgreSQL on bizera-wsl while Podman networking is unavailable. Reads `.env`; never prints secrets.

`scripts/phase5-smoke.sh` — live smoke for the Phase 5 contract (001B §8): `/raw` insert/duplicate/reject, an `/ingest` record with `payload_hash`, `/provenance/{key}`, coverage, and a `raw_days=0&days=0` purge no-op. `RESTART=1` restarts `bst-lens-api` first; `LENS_URL` overrides the base URL (default `http://127.0.0.1:7710`). Reads the token from `.env`; never prints it.

`install-service.sh` — installs/refreshes the `bst-lens-api` systemd unit (`bst-lens-api.service` + `run-service.sh`) so the API starts with WSL and restarts on failure.

## MCP adapter — `POST /mcp` (lens-api 0.4.0)

Model Context Protocol over Streamable HTTP so BST agents (via BizEra MCP Hub, Claude Code,
or any MCP client) query Social Lens with tools. Read-only; same bearer token as the REST API;
no server-initiated stream (`GET /mcp` → 405). Dependency-free JSON-RPC dispatcher in
`app/mcp.py` — `initialize`, `ping`, `tools/list`, `tools/call`, notifications, batches.

| Tool | Purpose |
|---|---|
| `search_records` | substring text search (Lao/Thai/EN, ILIKE) + platform / record_type / container_id / keyword / since / matched_only filters, newest first, limit ≤ 200 |
| `get_record` | one record by `platform:post_id` with its comments |
| `get_stats` | totals, matched, seen links, by platform/type, last capture |
| `top_containers` | groups/hashtags ranked by matched records |
| `list_seen` | seen-link frontier rows by status |

Register in a client (Claude Code `.mcp.json` / MCP Hub gateway):

```json
{
  "mcpServers": {
    "social-lens": {
      "type": "http",
      "url": "http://localhost:7710/mcp",
      "headers": { "Authorization": "Bearer <LENS_API_TOKEN from services/lens-api/.env>" }
    }
  }
}
```

Smoke test against a running server (token read from `.env`): `bash services/lens-api/mcp-smoke.sh`.
Verified live on bizera-wsl 2026-09-17: 401 without token; initialize / tools/list / all five tools OK.

## L0 raw evidence + provenance (Phase 5)

`POST /raw` stores payloads keyed by SHA-256 (verified server-side; `LENS_RAW_MAX_BYTES`); `/ingest` records may carry `payload_hash`/`content_hash` and always produce a `capture_events` row. `GET /provenance/{key}` shows a record's sightings and whether its raw body is still present; `GET /provenance` reports coverage (Phase 5 exit metric).

## Retention (data minimisation, ordered)

1. `LENS_RAW_RETENTION_DAYS` (default **90**) — raw payload bodies are nulled; the hash row stays forever.
2. `LENS_RECORD_RETENTION_DAYS` (default **730**; `0` disables) — records whose post date (else capture date) is older are deleted **unless `protected`**, plus frontier rows no record references. Capture events are kept.

Runs at startup and every 24 h in-process; `POST /admin/purge?raw_days=&days=` (bearer) runs both now.
