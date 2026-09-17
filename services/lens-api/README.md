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
| `LENS_RESOLUTION_ENABLED` | `0` | `1` applies `schema_market.sql` and enables the 001E resolution run, `/market/*`, `/resolution/stats`, `/admin/resolve` — keep off until 001E freezes |
| `LENS_RESOLVE_INTERVAL_MIN` | `30` | resolution loop period (0 = off; only with the flag) |
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

## Extraction rules — `app/extract/` (Phase 6, 001C)

`extract_observation(text, record_type, author_name, post_date, fx, contact_salt)` → `Observation` (signal class, asset type, claims, price observations). Pure Python, deterministic, no I/O; `fx(currency, date)` is injected so LAK normalisation is testable. Keyword groups live in `app/extract/keywords.py` and are mirrored in the extension (parity-tested). Persisted in the `extract` schema (`app/schema_extract.sql`, applied after `schema.sql`; kept additive by `scripts/schema_lint.py`, which CI runs).

Run model: a background loop (`LENS_EXTRACT_INTERVAL_MIN`, default 15, 0 = off) extracts records that have no current observation, whose `content_hash` changed, or whose observation is from an older `method_version`. Runs append; nothing is updated or deleted; one run at a time.

| Endpoint | Semantics |
|---|---|
| `POST /admin/extract?since=&limit=&force=` | run now; `force=1` re-runs the current method version on already-observed records (new observations, old kept) |
| `GET /observations?class=&asset=&since=&min_conf=&limit=` | current observations (one per record) |
| `GET /observations/{key}?all=1` | current observation + claims + price observations; `all=1` returns every run's observation |
| `GET /extract/stats` | by class / asset, UNCERTAIN share, `claims_without_confidence` (must be 0), price observations lacking FX, last runs |
| `POST /admin/fx?currency=USD|THB&rate_date=&lak_per_unit=&source=BOL_REFERENCE|MANUAL` | load one FX reference rate (C2); rates within 7 days before the post date are used |

Contact points (D5/C4): `LENS_CONTACT_SALT` (default derived from the API token) salts the hash; `LENS_CONTACT_KEY` encrypts the raw value with pgcrypto — unset ⇒ masked + hash only. Raw values are never in claims JSON and never returned by any endpoint or MCP tool. Tests: `tests/test_extract_rules.py`; the golden-fixture gate reads `tests/fixtures/extract/golden-v1.jsonl` when it exists.

## Geography — `app/geo/` (Phase 6, 001D)

Gazetteer = the current `geo.admin_versions` row (immutable copies of the BST Lao Data Map export, D4) loaded in memory; `GEO_RULE_V1` resolves an observation's `LOCATION_TEXT` / `MAP_URL` / `COORDINATE` claims to admin codes + a point with an explicit `precision` and `confidence`, inside the extraction run. Without PostGIS (G1) the point path keeps the pin and takes admin codes only from agreement with the text path — always named in `signals`.

| Endpoint | Semantics |
|---|---|
| `POST /admin/geo/import?source_ref=&make_current=` | JSON body `{admin_version, provinces[], districts[], villages[], aliases[]?}`; versions are immutable (409 on re-import) |
| `POST /admin/geo/alias?level=&code=&alias=` | add a spelling collected from review (G3) |
| `GET /geo/stats` | current version, gazetteer size, primary precision distribution, `precision_assigned_share` (001D §9.5 — must be 1.0), unresolved share |
| `GET /geo/resolve?text=` | dry run, no write |

PostGIS (G1): `scripts/install-postgis.sh` on the cluster, then restart — `schema_postgis.sql` is applied automatically when the extension exists and pins resolve via `ST_Within`. Lao Data Map: `scripts/geo_import.py convert|check|post`.

Golden sets (C5): `python scripts/golden.py export --out DIR --limit 120` writes masked CSVs to label; `python scripts/golden.py build --records … --locations …` writes the fixtures the gates read (`tests/fixtures/extract/golden-v1.jsonl`, `tests/fixtures/geo/golden-v1.jsonl`).

## Entity resolution — `app/resolution/` (Phase 7, 001E) — wired behind a flag

Pure modules: `canonical_permalink(url)` / `post_identity(url)` (001E §3), `jaccard_3gram` / `simhash64` / `hamming` (§4/§6), `score_pair(Side, Side) -> Score` (`MATCH_V1`, §6; weights uncalibrated until the §11 reviewed sample), `build_clusters([ClusterInput]) -> (clusters, evidence)` (§4), `block([Side]) -> BlockingResult` (§5).

Run (`service.run_resolution`, `store.ResolutionStore`; **only when `LENS_RESOLUTION_ENABLED=1`**, which also applies the 001F draft `schema_market.sql` at startup): load current SALE/RENT observations with primary location, size claims, price and contact hashes → listing clusters (append-only, deterministic ids) → blocking → `MATCH_V1` scoring → decisions in observation order: a `HIGH_CONFIDENCE_MATCH` links the observation to the best-scoring peer's market property (ties by property id); otherwise the observation opens or keeps its own `MP-` property as `SEPARATE_CANDIDATE`, or `REVIEW_REQUIRED` when a peer scored in the review band — every scored pair is stored as a candidate with its signals. Observations with a `HUMAN` decision are never re-decided (E4); already-decided ones only with `force=1` (new decision superseding the old — nothing edited). Each run ends with a `market.property_stats_snapshots` row per touched property (asking min/max/median/latest/dispersion per price type, advertiser and cluster counts, resolution confidence, review state). Background loop `LENS_RESOLVE_INTERVAL_MIN` (default 30, 0 = off). Off (the production default) ⇒ no schema applied, endpoints 404, MCP tools error.

| Endpoint (flag on) | Semantics |
|---|---|
| `POST /admin/resolve?force=` | run now |
| `GET /market/properties?status=&min_obs=&limit=` | properties with their latest snapshot |
| `GET /market/properties/{mp}` | property + current observations (record keys, decisions, scores) + snapshot history |
| `GET /resolution/stats` | run history, decisions by state/source, candidates, `unexplained_machine_decisions` (must be 0: stored score ≠ sum of signal contributions) |
| `POST /admin/quality/recompute?since=` | new stats + DQ snapshot rows for ACTIVE properties (all, or observed after `since`) — 001G §7 |

MCP: `search_market_properties`, `get_market_property`, `resolution_stats`. Live test: `LENS_RESOLUTION_ENABLED=1 LENS_TEST_DSN=… pytest tests/test_resolution_live.py` (four advertisers + a re-post of one 20×30 Dongdok parcel land on one property with all five prices in the snapshot; a Pakse decoy stays separate; a human `UNLINKED` survives a forced re-run).

## Quality & publication — `app/quality/`, `app/publish/` (Phase 8, 001G/001H) — pure modules

`score_observation(DqInput) -> DqResult` (DQ 0–100, grade A–D, per-component reasons), `property_dq()`, `evaluate_rules()` (validation exceptions). `check_records()` / `check_bundle_text()` are the export negative check (no contacts, author hashes, text, media, permalinks, stray hashes). `audit.events` (`schema_audit.sql`) is applied at startup. `GET /quality/stats` (and MCP `quality_stats`) reports DQ grades and exceptions over current observations. Review until the Portal: `python scripts/review.py export --queue extraction|location|match --out DIR` → fill `action`/`corrected_value`/`reason` → `python scripts/review.py import --file DIR/review-<queue>.csv --reviewer <id> [--dry-run]` (superseding HUMAN rows + one audit row per action; CSVs are never committed). The match queue needs `resolution.*` (flag on): items are `REVIEW_REQUIRED` observations with their best candidate; CONFIRM/CORRECT = *same property* → a MERGE (the property with more observations survives, the other becomes `SUPERSEDED`, every moved observation gets a HUMAN `CONFIRMED` row); REJECT = own property confirmed (the rejected target goes in the audit row); statistics snapshots are recomputed for every touched property. Property DQ (001G §2) is written on every `market.property_stats_snapshots` row (`dq_grade`); `POST /admin/quality/recompute?since=` (flag on) writes fresh snapshot rows for ACTIVE properties. Publish endpoints follow the 001E/001F freezes.

## Schema files and lint

`app/schema.sql` = L0/L1 (core); `app/schema_<layer>.sql` = L2/L3 (`schema_extract.sql`, `schema_geo.sql`, `schema_audit.sql` applied; `schema_market.sql` is the 001F draft, linted; applied only with `LENS_RESOLUTION_ENABLED=1` until 001E freezes), additive only. `python scripts/schema_lint.py app` (also `tests/test_schema_lint.py`) rejects L2 statements that touch L0/L1 tables, bare fact-like column names (`price`, `owner`, `property`, `area`, `parcel`, `title`, BizProp+ ids), observation/claim tables without a NOT NULL 0..1 `confidence`, DROP/DELETE in the core file, claims JSON that could carry a raw contact value, price-like columns on `market.properties`, snapshot tables without `stats_version`/`computed_at`, and decision/link tables without `supersedes_*` (or with `updated_at`). `LENS_CONTACT_KEY` (optional) is the pgcrypto key for `extract.contact_points.raw_value_enc`; unset ⇒ raw contact values are not stored (masked + hash only).

## Retention (data minimisation, ordered)

1. `LENS_RAW_RETENTION_DAYS` (default **90**) — raw payload bodies are nulled; the hash row stays forever.
2. `LENS_RECORD_RETENTION_DAYS` (default **730**; `0` disables) — records whose post date (else capture date) is older are deleted **unless `protected`**, plus frontier rows no record references. Capture events are kept.

Runs at startup and every 24 h in-process; `POST /admin/purge?raw_days=&days=` (bearer) runs both now.
