# Changelog

All notable changes to BST Social Lens are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versioning is [SemVer](https://semver.org/).

## [Unreleased]

### Added
- **Phase 6 (d) — extraction run loop + L2 read API (001C §8). lens-api → 0.6.0.** In-process loop every `LENS_EXTRACT_INTERVAL_MIN` (15; 0 = off) runs RULE_V1 over records with no current observation, a changed `content_hash`, or an older `method_version` — idempotent, append-only, one run at a time. `POST /admin/extract?since=&limit=&force=` (force = re-run current version, old observations kept); `GET /observations?class=&asset=&since=&min_conf=&limit=`; `GET /observations/{key}?all=1`; `GET /extract/stats` (by class/asset, UNCERTAIN share, `claims_without_confidence`, price observations without FX, recent runs); `POST /admin/fx?currency=&rate_date=&lak_per_unit=&source=` (C2). Contact points: salted hash (`LENS_CONTACT_SALT`, default derived from the token), masked value, raw value only as `pgp_sym_encrypt(raw, LENS_CONTACT_KEY)` when the key is set — never in claims JSON, never returned by any endpoint. `/mcp` gains read-only `list_observations`, `get_observation`, `extraction_stats`. Live end-to-end test (`tests/test_extract_live.py`, `LENS_TEST_DSN`) covers run → rows → idempotency → force → FX → encrypted contact → HTTP/MCP reads → L1 untouched.
- Fix: MCP `structuredContent` now round-trips through JSON so tool results with datetimes/Decimals serialise.
- **Phase 6 (c) — `extract.*` DDL + schema-lint.** `services/lens-api/app/schema_extract.sql` (additive, applied by `init_db()` after the core schema): `extract.runs`, `observations` (UNIQUE record_key+run, CHECKed class/asset vocab and 0..1 confidences), `claims` (field vocab CHECK, UTF-16 span, method/version/confidence/review_status NOT NULL, `normalised` JSONB may never carry `raw_value`, `supersedes_claim_id` for HUMAN corrections), `price_observations` (ASKING/WANTED types only, FX columns, `amount_lak` ⇔ `fx_rate`), `fx_rates` (BOL_REFERENCE/MANUAL), `contact_points` (hash PK, masked value, `raw_value_enc` via pgcrypto — C4) + `contact_sightings`, view `current_observations`. `pgcrypto` is created best-effort (startup never blocks). Validated on PostgreSQL 16 (idempotent re-apply, constraint probes); `tests/test_schema_live.py` runs when `LENS_TEST_DSN` is set.
- **CI schema-lint** (`scripts/schema_lint.py`, step in the required `lens-api · pytest` job + `tests/test_schema_lint.py`): R1 L2 files never touch L0/L1 tables; R2 no bare `price/owner/property/area/parcel/title` or BizProp+ ids; R3 observation/claim tables carry NOT NULL 0..1 confidence; R4 core schema has no DROP/DELETE/TRUNCATE and no L2 objects; R5 claims forbid `raw_value` in JSON.
- **Phase 6 (a) — RULE_V1 extraction rules module (SLL-PROP-DATA-001C §5–§7).** `services/lens-api/app/extract/`: pure, deterministic, no DB. Keyword groups (authoritative copy, `KEYWORD_GROUPS_VERSION 1.0.0`), signal-class decision table + adjustments, asset-type scoring, field extractors (price with Lao magnitudes/digits/currencies and per-m²/month/year basis, area from `n x m` and Lao/Thai/metric units, contact points hashed + masked with raw in a separate key, location text by admin level, map URLs + coordinates in the Laos bbox, title mentions, transaction type, advertiser role), LAK normalisation through an injectable FX lookup (`NO_FX` when absent), append-only price observations with per-m² when area is known, UTF-16 evidence spans. 60 pytest on synthetic Lao/Thai/English posts; golden-fixture gate (§10.1) present but skipped until real data is labelled. Not wired to the API or a table yet (001F DDL next).
- Extension: `KEYWORD_GROUPS` / `keywordGroupHits()` / `leadLabel()` in `src/lib/keywords.ts` (lead tagging only, not classification) and `tests/keyword-groups-parity.test.ts`, which fails CI if the extension and server copies drift (001C §10.4). `DEFAULT_KEYWORD_SET` and `storeMode` unchanged (D2 flip lands with the extraction API).
- **Phase 5 — Capture & Provenance (SLL-PROP-DATA-001B, ADR-0005).** L0 now lives on the server: `POST /raw` stores raw payloads keyed by SHA-256 (server-verified, size-capped, duplicates counted not overwritten); every `/ingest` record produces a `capture_events` row; records gain `content_hash`, `first_payload_hash` (immutable), `last_payload_hash`, `capture_count`, `protected`. `GET /provenance/{key}` and `GET /provenance` (coverage metric). Retention is now ordered: raw **bodies** null after `LENS_RAW_RETENTION_DAYS` (90; hash + context kept), records after `LENS_RECORD_RETENTION_DAYS` (730) unless `protected`; `/admin/purge?raw_days=&days=`. lens-api → 0.5.0.
- Extension **0.7.0**: `payload_hash` + `content_hash` on every record; Dexie v3 (`raw.synced`, `payload_hash`, `truncated`); `sendRaw` setting (default on) pushes raw evidence after each sync in ≤ 25-row / ≤ 4 MB batches (`src/lib/provenance.ts`, tested); panel toggle + raw-unsynced count.
- **lens-api — retention purge**: `LENS_RETENTION_DAYS` (default 730, 0 = off) deletes records older than N days by post date (else capture date) plus orphaned frontier rows; runs at startup + daily; `POST /admin/purge?days=N` runs it on demand.
- **lens-api 0.4.0 — MCP adapter** (`POST /mcp`, ADR-0004 Phase 4 item): Model Context Protocol over Streamable HTTP with read-only tools `search_records`, `get_record`, `get_stats`, `top_containers`, `list_seen`; bearer-token gated; dependency-free JSON-RPC dispatcher (`app/mcp.py`); 5 pytest + live smoke script (`mcp-smoke.sh`). Lets BST agents query Social Lens through MCP Hub / Claude Code.

## [0.5.0] — 2026-09-16

### Added
- **Social Lens Console** (`console/index.html`) — a lead-intelligence dashboard served by lens-api at `http://localhost:7710/` (same-origin, no CORS). KPI tiles (total / matched / posts / comments / seen links / last capture), filters (search, platform, record type, source type, keyword facets, date range), a Lao-aware leads table with in-text keyword highlighting, top-sources bars, and CSV export. Falls back to sample data when the API is unreachable, so the page is never empty. Light/dark, responsive.
- lens-api serves the Console via `StaticFiles` at `/` when `LENS_CONSOLE_DIR` exists; Dockerfile now bundles `console/` (build context moved to repo root).

## [0.4.0] — 2026-09-16

### Added
- **Autonomous mode (auto-scroll)** — a Start/Stop button in the side panel drives the current Facebook/TikTok tab with human-like jittered pacing (2.5–5s) so the feed lazy-loads and the interceptor captures it. Auto-stops at caps (default 40 scrolls / 10 min) or end of feed; pauses while the tab is hidden; stops on page unload. Scroll only — no clicking or navigation.
- `src/lib/autorun.ts`: pure, unit-tested pacing/stop logic (`nextDelay`, `shouldStop`); configurable caps in settings (`autoRun`).
- `autoscroll.content.ts` + background relay (`autoStart`/`autoStop`/`autoState`); live scroll count and stop reason in the side panel.

### Notes
- Manual mode remains the default; both modes coexist and share one capture/keyword/seen pipeline.
- Auto-scroll does not follow links or expand hidden comment threads — that is the Phase-4 assisted-navigation step (ADR-0003).

## [0.3.0] — 2026-09-16

### Added
- **Keyword-set ingestion** (`src/lib/keywords.ts`): Lao-aware matcher — NFC-normalized, case-folded, substring (no word boundaries), so ຂາຍດິນ matches both ຂາຍ and ດິນ. Named `KeywordSet` (include/exclude/min_hits) editable in the side panel and synced to the server. Records tagged `matched_keywords`, `match_score`, `matched_via`. **Store mode** `matched` (default) | `all`.
- **Comment ingestion**: `record_type: 'post' | 'comment'` with `parent_post_id`; Facebook (`Comment` nodes) and TikTok (`/api/comment/list/`) comment parsers. A matching comment promotes its parent post.
- **Seen-link frontier** (`src/lib/url.ts` + `seen_links`): `url_hash = sha256(normalizeUrl(permalink))`; URL normalization strips tracking params and canonicalizes FB permalink forms. `seenCheck`/`seenMark` messages; server `POST/GET /seen` reconcile across machines. Prevents re-opening a link already processed.
- **`container_type`** (group | page | profile | feed | hashtag | search) on records — a query filter alongside Post Date (`created_at`).
- lens-api: `/seen` endpoints; `/stats` now reports matched, seen, and by-type counts.
- Tests: keyword matcher (Lao fixtures), URL normalizer, comment ingest, `/seen`; upsert + seen verified on live Postgres 16.

### Changed
- Facebook + TikTok parsers → 0.3.0 (emit record_type, container_type, comments).
- Dexie schema v2 (seen table, record_type/parent indexes). Extension + LensDB schema extended; existing rows default `record_type='post'`.
- Extension → 0.3.0.

## [0.2.0] — 2026-09-16

### Added
- **Server tier (`services/lens-api`)**: FastAPI ingest API + PostgreSQL system of record (LensDB), Podman compose. Endpoints: `/health`, `/ingest`, `/records`, `/stats`. Bearer auth. SQL upsert dedup on `platform:post_id`, verified against live Postgres 16.
- **ADR-0001** documenting the three-tier topology (extension buffer → lens-api → LensDB).
- Raw-payload NDJSON export in the side panel, for building parser fixtures.
- Live re-parse test (`tests/live.test.ts`) that runs the parser over real exported payloads and reports field fill rates.

### Changed
- **Facebook parser → 0.2.0**, hardened against the live 2026-09 group-feed GraphQL shape: engagement counts read from `comet_sections…adaptive_ufi_action_renderers`; group resolved from `story.to` / `associated_group` (fixes `container_id="feed"` on the aggregated feed); media walks comet content attachments; author falls back to `feedback.owning_profile`.
- Hashtag extraction now includes Unicode marks (`\p{M}`) for Lao/Thai scripts.
- Extension version → 0.2.0.

### Validated
- 10 records parsed from 28 live Facebook group-feed payloads; 100% fill on ids, author, timestamp, and engagement counts.

## [0.1.0] — 2026-09-16

### Added
- Phase 0 scaffold: WXT MV3 extension building for Chrome and Edge from one codebase.
- MAIN-world interceptor hooking `fetch`/`XHR` for Facebook GraphQL and TikTok API responses; isolated-world bridge; on-page capture badge.
- Background service worker: Dexie/IndexedDB store, dedup, ndjson/CSV export, ingest sync, raw-payload retention.
- Facebook and TikTok platform modules with fixture tests.
- Svelte side panel: stats, export, ingest settings, privacy toggles.
- AGENTS.md, SDLC docs skeleton, research brief, Apache-2.0 license.
