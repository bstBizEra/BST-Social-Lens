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
