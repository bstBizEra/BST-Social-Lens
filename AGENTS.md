# AGENTS.md — BST Social Lens

Operating manual for AI agents and engineers working in this repository. Follows the BizEra enterprise SDLC governance structure.

## 1. Project identity

| Field | Value |
|---|---|
| Name | BST Social Lens (`bst-social-lens`) |
| Family | BST product line (alongside BST Intel Bridge, BST Lao Data Map, BST Neuraxis) |
| Purpose | Chrome/Edge (MV3) extension that captures Facebook group/post and TikTok signals from the operator's own browser session into a local IndexedDB store, exports ndjson/CSV, and pushes to the BST ingest API for the Social Lens Console artifact |
| Governance state | `DESIGN_READY` → target `IMPLEMENTATION_READY` after Gate 6 |
| Owner | OP-Vily / BizEra |

## 2. Repository layout

```
src/
  entrypoints/
    interceptor.content.ts   MAIN-world fetch/XHR hook (no extension APIs here)
    bridge.content.ts        isolated-world relay → chrome.runtime + on-page badge
    background.ts            service worker: routing, Dexie persistence, export, ingest sync
    sidepanel/               Svelte 5 side panel (stats, export, ingest settings, privacy)
  lib/
    types.ts                 SocialRecord v1 schema, message contracts, Settings
    db/                      Dexie store (records, raw, runs, settings)
    modules/                 one PlatformModule per platform (facebook.ts, tiktok.ts)
    export.ts                CSV / NDJSON serialisers
tests/                       vitest parser tests + JSON fixtures (+ live.test.ts)
services/lens-api/           server tier — FastAPI ingest + Postgres (LensDB), Podman compose
docs/00..12-*                SDLC gate artefacts (see §5), incl. ADR-0001 topology
public/icon                  extension icons
wxt.config.ts                manifest + build config (chrome default, `-b edge`)
```

## 3. Non-negotiable engineering rules

1. **MV3 constraints.** No `webRequestBlocking`, no remote code, listeners registered at top level in `background.ts`, no in-memory state that matters — IndexedDB is the source of truth.
2. **Interceptor safety.** `interceptor.content.ts` must never throw into the page. Wrap everything; on failure return the original response untouched.
3. **Platform modules never throw.** `parse()` returns `[]` on unexpected shapes; the raw payload is retained for re-parse. Bump `version` on every output change.
4. **Privacy by default.** `hashAuthorIds` is on; raw payloads purge after `rawRetentionDays`. Do not add member-profile aggregation (names + contacts + location) — post-level content and engagement only.
5. **Account safety.** Any future auto-scroll must use jittered 2–5 s pacing, per-run caps, and run only while the side panel is open. No background autonomous browsing in v0.x.
6. **Store-listing language.** Never use "scraper"/"crawler" in `manifest.name`, `description`, README headline, or store copy.
7. **Tests before merge.** `npm run check && npm test && npm run build && npm run build:edge` must pass (CI: `.github/workflows/parser-health.yml` on every PR).
8. **Parser health.** Every new fixture in `tests/fixtures/*.json` needs a `FIXTURE_PAGES` entry in `tests/parser-health.test.ts`. Lowering a threshold requires a CHANGELOG note. Weekly: export raw NDJSON from the side panel → `tests/fixtures/_live/` → `npm run health`; promote a sanitised slice to a committed fixture when it shows a new payload shape.

## 4. Commands

| Command | Purpose |
|---|---|
| `npm install` | installs deps and runs `wxt prepare` (generates `.wxt/` types) |
| `npm run dev` / `npm run dev:edge` | dev build with HMR (load `dist/chrome-mv3` or `dist/edge-mv3` unpacked) |
| `npm run build` / `npm run build:edge` | production build |
| `npm run zip` / `npm run zip:edge` | store-ready zip |
| `npm run check` | svelte-check + tsc |
| `npm test` | vitest parser tests |
| `npm run health` | parser-health fixture replay with fill-rate thresholds (`tests/parser-health.thresholds.json`); also runs weekly in CI |

## 5. SDLC gates and where artefacts go

| Gate | Folder | Status |
|---|---|---|
| 1 PRD Review | `docs/01-product-requirements` | pending — PRD to be authored from the research brief |
| 2 Requirement Decomposition | `docs/01-product-requirements` | pending |
| 3 Architecture Design | `docs/03-architecture` | **done** — `research-brief-base-stack.md`, `adr-0001-lensdb-ingest-topology.md` |
| 4 Detailed Solution Design | `docs/03-architecture`, `docs/04-data-api-integration` | **done** — schema v1 in `src/lib/types.ts`; ingest contract + LensDB schema in `services/lens-api` |
| 5 Security/Compliance Design | `docs/05-security-privacy` | pending — Lao EDPL 2017 mapping, ToS exposure register |
| 6 Implementation Planning | `docs/07-engineering-devsecops` | pending |
| 7 Development | `src/`, `services/` | extension 0.2.0; lens-api 0.2.0 (ingest, upsert, auth) |
| 8 Engineering Verification | `tests/` | extension: 6 vitest incl. live re-parse; lens-api: 5 pytest + live Postgres upsert check |
| 9–14 | `docs/08..12` | not started |

## 6. Known risks / open items

- Facebook GraphQL `Story` shape varies by surface (group feed vs permalink vs page); parser was written against representative shapes and must be validated against captured raw payloads from real Lao groups.
- TikTok comment payloads (`/api/comment/list/`) are captured raw but not yet normalised.
- TikTok parser still validated only on a synthetic fixture — needs a live smoke test.
- lens-api `/mcp` adapter for MCP Hub proxying is deferred to Phase 4; auth is a static bearer token until then.
- Side panel requires Chrome/Edge ≥ 116 (`chrome.sidePanel`).

## 7. Lessons log

Record mistakes and their fixes here so they carry forward.

- 2026-09-16 — `wxt prepare` runs on `postinstall` and fails if `wxt.config.ts` is missing: write config before the first `npm install`.
- 2026-09-16 — Fixture epoch timestamps must match the assertion year; a wrong fixture made a correct parser look broken.
- 2026-09-16 — Live FB group feed nests engagement under `comet_sections…adaptive_ufi_action_renderers`, not on `feedback` directly; walk both subtrees. Always harden parsers against exported raw payloads, not just hand-written fixtures.
- 2026-09-16 — Postgres refuses to run as root; integration tests spin the cluster under an unprivileged user.

## 8. Server tier (services/lens-api)

FastAPI ingest + PostgreSQL (LensDB) on `bizera-wsl` under Podman. `lens-db` has no published ports; `lens-api` is published on `127.0.0.1:7710` only. Dedup via SQL upsert on `platform:post_id`. See `services/lens-api/README.md` and `docs/03-architecture/adr-0001-lensdb-ingest-topology.md`. Test: `cd services/lens-api && pip install -r requirements-dev.txt && pytest -q`.
