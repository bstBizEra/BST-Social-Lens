# BST Social Lens

A Chrome / Microsoft Edge (Manifest V3) extension that captures social signals — Facebook group and post data, TikTok videos and engagement — from **your own logged-in browser session**, stores them locally, and exports them (CSV / NDJSON) or pushes them to the BST ingest API for the Social Lens Console.

Part of the **BST** product family by BizEra.

## How it works

```
Facebook / TikTok tab
  └─ interceptor (MAIN world)  hooks fetch/XHR → clones matching JSON responses
       └─ bridge (isolated)     validates → chrome.runtime.sendMessage
            └─ background SW    routes to platform module → parse → Dexie (IndexedDB)
                 ├─ side panel  live stats · export CSV/NDJSON · settings
                 └─ ingest API  POST /ingest (FastAPI on bizera-wsl) → Console artifact
```

Chrome/Edge MV3 cannot read response bodies with `webRequest`, so capture happens from a page-world script that wraps `window.fetch` and `XMLHttpRequest`. No credentials are handled; the extension only sees what the page already loaded for you.

## Quick start (development)

Requirements: Node ≥ 20, npm ≥ 10, Chrome or Edge ≥ 116.

```bash
npm install          # also runs `wxt prepare`
npm run dev          # Chrome dev build with HMR → dist/chrome-mv3
npm run dev:edge     # Edge dev build           → dist/edge-mv3
```

Load unpacked:

- **Edge:** `edge://extensions` → enable *Developer mode* → *Load unpacked* → select `dist/edge-mv3`
- **Chrome:** `chrome://extensions` → *Developer mode* → *Load unpacked* → select `dist/chrome-mv3`

Then open a Facebook group or a TikTok profile/hashtag page and scroll. A small badge at the bottom-right shows records and payloads captured; click the toolbar icon to open the side panel.

## Production build

```bash
npm run build && npm run build:edge   # dist/chrome-mv3, dist/edge-mv3
npm run zip && npm run zip:edge       # store-ready zips in dist/
```

## Verify

```bash
npm run check   # svelte-check + tsc
npm test        # vitest parser tests (tests/fixtures)
```

## Data model (v1)

`platform, post_id, permalink, container_id, container_name, author_name, author_hash (author_id optional), author_url, text, lang, created_at, captured_at, reactions_total, comments_count, shares_count, views_count, media[], hashtags[], parser_version, synced`

Author IDs are hashed (SHA-256) by default; raw payloads are retained 30 days for re-parsing and then purged. Post-level content and engagement only — the extension does not build member profiles.

## Responsible use

Use a secondary account, keep runs small, and respect the Terms of Service of each platform and applicable data-protection law (including the Lao PDR Law on Electronic Data Protection, 2017). This tool is intended for market and community intelligence on content you can already see.

## Documentation

See [`AGENTS.md`](./AGENTS.md) for the engineering rules and SDLC gate map, and `docs/` for gate artefacts. The base-stack research brief is in `docs/03-architecture/`.

## License

Apache-2.0 © 2026 BizEra / BST
