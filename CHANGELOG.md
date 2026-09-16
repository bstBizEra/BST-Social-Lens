# Changelog

All notable changes to BST Social Lens are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versioning is [SemVer](https://semver.org/).

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
