# BST Social Lens — Base Repository & Extension Research Brief

**Date:** 2026-09-16 · **Owner:** OP-Vily / BizEra · **Scope:** Chrome + Microsoft Edge (Manifest V3) extension for Facebook groups/posts and TikTok capture

---

## 1. Executive Summary

- **No existing open-source extension is fit to fork as-is.** The Facebook-group extension repos are hobby-scale (≤5 stars, 4 commits, no license or no tests). The one production-grade reference (Zeeschuimer) is Firefox-only Manifest V2 and does not support Facebook.
- **Recommended path:** build BST Social Lens as a **new WXT (TypeScript, Vite) project**, borrowing three proven patterns: Zeeschuimer's *module-per-platform* capture architecture and export model, silverbirder's *MAIN-world fetch/XHR hook* for TikTok API interception on MV3, and the DOM-scroll capture pattern from the Facebook-group extensions as a fallback.
- **Capture strategy:** JSON-intercept first (Facebook GraphQL + TikTok `/api/*` responses) with DOM parsing as fallback. On Chrome/Edge MV3, `webRequest` cannot read response bodies, so interception must be done from a page-world script — this is the single most important architectural constraint.
- **Edge support is free:** WXT builds Chrome and Edge from the same MV3 output; Edge Add-ons store accepts the identical package.

---

## 2. Candidate Landscape

### 2.1 Browser-extension candidates

| Candidate | Platforms | Capture method | Manifest | Browser | License | Health | Verdict |
|---|---|---|---|---|---|---|---|
| **Zeeschuimer** (Digital Methods Initiative) | TikTok, Instagram, X, Threads, Douyin, Pinterest, RedNote, etc. — **no Facebook** | `webRequest` + `filterResponseData` (response-body interception) | **MV2** (`webRequestBlocking`, `<all_urls>`) | **Firefox only** | MPL-2.0 | Active; v1.14.3; 4CAT integration | **Reference architecture, not a fork base.** MV2/Firefox-only means a Chrome/Edge port is a rewrite of the capture layer. Its per-platform module pattern and ndjson/CSV export are worth copying. |
| **InsightSocial** | Facebook (groups, pages, profiles, feed, single post), TikTok (hashtag, profile, video, search, FYP), IG, X, LinkedIn, Threads | Simulated human scrolling in user session; IndexedDB local store; optional backend sync | Not documented | Chrome | Not visible (no LICENSE detected) | 2 stars, 7 commits | Closest feature match, but unknown license and near-zero community. Treat as a **feature checklist and UX reference** only. |
| **silverbirder / chrome-extensions-tiktok-scraping-downloader** | TikTok | Dual: page-world script reads window state + hooks TikTok API calls; posts to a user-defined server | **MV3** (service worker, `web_accessible_resources`) | Chrome | MIT | 26 stars, 36 commits; author warns of brittleness | **Best MV3 pattern reference for TikTok.** Copy the injection/hook technique and the "POST to endpoint" pipeline, not the code. |
| **4karam / Facebook-Scrapping-G-P** | Facebook groups | Content script + floating button; DOM parse | Unstated | Chrome | None declared | 5 stars, 4 commits, Dec 2025 | Fields: author, text, likes, comments → JSON. Too thin to fork; useful as a DOM-selector starting point. |
| **DevSkits916 / facebook-groups-exporter** | Facebook group *list* only | DOM | **MV3** | Chrome | MIT | 4 commits | Collects group names/URLs, not posts. Minimal. |
| **Facebook Group & Page Post Scraper** (Chrome Web Store, whoareyouanas.com) | Facebook groups + pages | Local DOM capture | MV3 (store-listed) | Chrome/Edge | Proprietary; free tier 25 posts, Pro $19/mo | v1.1.19, updated 2026-06-28, 811 users, 4.3★ | **Commercial benchmark.** Field set = target schema for v1: author + profile URL, text, reactions/comments/shares, media URLs, permalink, timestamp, comments. |

### 2.2 Non-extension references (parser logic, not architecture)

| Repo | Type | Value to Social Lens |
|---|---|---|
| kevinzg/facebook-scraper | Python, mbasic HTML | Field normalisation model for posts/groups; largely broken by Meta's mbasic shutdown — do not depend on it |
| MasuRii/FBScrapeIdeas | Python CLI (Playwright) | Group posts + comments → CSV/JSON; categorisation UX ideas |
| drawrowfly/tiktok-scraper | Node CLI | TikTok metadata schema (user, hashtag, trends, music) |
| dfreelon/pyktok | Python | TikTok video/metadata collection for research; clean data model |
| cubernetes/TikTokCommentScraper | JS | Full comment-thread extraction logic |
| ctala/api-reverse-engineer | MV3 Chrome ext | Generic fetch/XHR interceptor with URL filter (e.g. `graphql`) — good scaffold for the hook layer |

### 2.3 Extension framework selection

| Framework | Cross-browser | MV3 | TypeScript | Notes |
|---|---|---|---|---|
| **WXT** (recommended) | Chrome, Edge, Firefox, Safari from one codebase; `wxt -b chrome` / `-b edge` | Default | Default | Vite HMR, typed manifest in `wxt.config.ts`, built-in store publishing; leading choice for new 2026 projects |
| Plasmo | Chrome-first; Firefox with extra config | Yes | Yes | React-centric; weaker cross-browser story |
| Extension.js | Chrome/Edge/Firefox | Yes | Yes | Manifest-first; browser-prefixed keys; more manual |

---

## 3. Critical Technical Finding — MV3 Capture Constraint

Zeeschuimer's capture model depends on Firefox's `webRequest.filterResponseData`, which lets an extension read response bodies. **Chrome/Edge MV3 has no equivalent**: `webRequest` is observe-only (headers), `webRequestBlocking` is gone, and `declarativeNetRequest` cannot read bodies.

The workable MV3 pattern (used by silverbirder and api-reverse-engineer):

1. Register a content script with `"world": "MAIN"` at `document_start` (or inject via `web_accessible_resources`).
2. Monkey-patch `window.fetch` and `XMLHttpRequest.prototype.open/send` to clone and parse responses whose URL matches `/api/graphql/` (Facebook) or `/api/post/item_list`, `/api/comment/list`, `/api/search/` (TikTok).
3. Forward parsed JSON via `window.postMessage` → isolated-world content script → `chrome.runtime.sendMessage` → service worker → IndexedDB queue → ingest API.

Limitation: hooks miss requests made inside Web Workers and the server-rendered initial payload; the fallback is reading embedded state (`SIGI_STATE` / `__UNIVERSAL_DATA_FOR_REHYDRATION__` on TikTok; inline `<script type="application/json">` relay payloads on Facebook) plus DOM parsing.

---

## 4. Recommended Base Stack for BST Social Lens

| Layer | Choice | Rationale |
|---|---|---|
| Framework | **WXT + TypeScript** | One codebase → Chrome + Edge builds, MV3 by default, typed APIs, store publishing |
| UI | Svelte or Preact (popup + side panel) | Small bundle; side panel (`chrome.sidePanel`) mirrors InsightSocial's UX |
| Capture core | Custom `modules/<platform>.ts` (Zeeschuimer pattern): `matches()`, `parse(payload)`, `normalise()` | Isolates breakage to one platform module |
| Interception | MAIN-world fetch/XHR hook + embedded-state reader + DOM fallback | See §3 |
| Local store | Dexie (IndexedDB) with dedupe on `platform:post_id` | Same lib Zeeschuimer uses; offline-safe |
| Throttle | Service-worker job queue; 2–5 s jittered scroll; per-run caps | Account-safety first |
| Export | ndjson + CSV (Zeeschuimer model) + `POST /ingest` to FastAPI on bizera-wsl | Artifact console reads from the API |
| Licensing | Apache-2.0 for the BST repo; only MIT/MPL-derived snippets, credited | Keeps commercial options open |

### Target schema (v1)

`platform, post_id, permalink, author_name, author_id, author_url, text, lang, created_at, captured_at, reactions_total, reactions_breakdown, comments_count, shares_count, views_count (TikTok), media[] {type,url}, hashtags[], group_id/hashtag_id, raw_ref`

Personal-data minimisation: store `author_id` hashed by default; raw payloads retained 30 days then purged.

---

## 5. Delivery Plan

| Phase | Scope | Output |
|---|---|---|
| **0 — Scaffold (2 days)** | `wxt init bst-social-lens --template svelte-ts`; MAIN-world hook; message bus; Dexie store | Extension loads in Chrome + Edge; captures raw GraphQL/TikTok JSON to IndexedDB |
| **1 — Facebook groups (1 week)** | `modules/facebook.ts` parser for group feed + single post; DOM fallback; export ndjson/CSV | Validated on 2 Lao groups; ≥95 % field fill rate |
| **2 — TikTok (1 week)** | `modules/tiktok.ts` for profile/hashtag/search + comment list; embedded-state reader | Validated on 3 accounts |
| **3 — Ingest + Console (1 week)** | FastAPI `/ingest`, `/targets`, `/runs`; Social Lens Console artifact; Intel Bridge hook (port 7700) | End-to-end: browser → API → console → BST agents |
| **4 — Hardening** | Throttle tuning, parse-failure telemetry, Edge Add-ons + CWS unlisted publishing | Internal release v0.1 |

---

## 6. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Meta/TikTok payload shape changes | Parser breaks silently | Version parsers; log unparsed payloads; parse-failure rate on console dashboard |
| Account checkpoint / rate limit | Loss of capture account | Secondary account; jittered pacing; hard caps per run; no background auto-runs in v1 |
| ToS exposure | Store listing removal | Unlisted/enterprise distribution; no "scraper" wording in listing; local-first data handling |
| Lao Electronic Data Protection Law (2017) | Personal-data liability | Post-level content only; hashed author IDs; no member-profile building; retention policy |
| MV3 service-worker lifecycle | Lost queue state | Persist queue in IndexedDB, not memory; register listeners at top level |

---

## 7. Sources

- Zeeschuimer — https://github.com/digitalmethodsinitiative/zeeschuimer (manifest: MV2, Firefox gecko settings)
- InsightSocial — https://github.com/insightsocialxyz/insightsocial
- silverbirder TikTok extension — https://github.com/silverbirder/chrome-extensions-tiktok-scraping-downloader
- 4karam Facebook group extension — https://github.com/4karam/Facebook-Scrapping-G-P
- DevSkits916 groups exporter — https://github.com/DevSkits916/facebook-groups-exporter
- Chrome Web Store: Facebook Group & Page Post Scraper — https://chromewebstore.google.com/detail/facebook-group-page-post/amlggiobnpdaadmoimbcambmmminkono
- kevinzg/facebook-scraper — https://github.com/kevinzg/facebook-scraper
- MasuRii/FBScrapeIdeas — https://github.com/MasuRii/FBScrapeIdeas
- drawrowfly/tiktok-scraper — https://github.com/drawrowfly/tiktok-scraper
- dfreelon/pyktok — https://github.com/dfreelon/pyktok
- cubernetes/TikTokCommentScraper — https://github.com/cubernetes/TikTokCommentScraper
- ctala/api-reverse-engineer — https://github.com/ctala/api-reverse-engineer
- WXT — https://wxt.dev/ and https://wxt.dev/guide/essentials/target-different-browsers
- Framework comparisons — https://kanopylabs.com/blog/wxt-vs-plasmo-vs-extension-js ; https://plugthis.ai/blog/chrome-extension-development-tools-comparison-2026
- Extension.js — https://extension.js.org/
- Chrome webRequest API (MV3) — https://developer.chrome.com/docs/extensions/reference/api/webRequest
- MV3 request interception discussion — https://stackoverflow.com/questions/67423002/intercepting-requests-in-chrome-extension-with-manifest-v3 ; https://groups.google.com/a/chromium.org/g/chromium-extensions/c/6P96hB6m9dY
- MV2 deprecation timeline — https://developer.chrome.com/docs/extensions/develop/migrate/mv2-deprecation-timeline
