# Changelog

All notable changes to BST Social Lens are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versioning is [SemVer](https://semver.org/).

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
