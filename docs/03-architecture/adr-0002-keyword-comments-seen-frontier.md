# ADR-0002: Keyword-set ingestion, comment records, and the seen-link frontier

- Status: Accepted
- Date: 2026-09-16
- Deciders: OP-Vily
- SDLC gate: 3/4 (Architecture / Detailed Solution Design)
- Supersedes parts of: none (extends ADR-0001)

## Context

Social Lens needed to move from "store everything I scroll past" to "watch for leads." Three requirements: (1) ingest by a Lao keyword set (e.g. ດິນ, ຂາຍ, ເຊົ່າ, ລາຄາ, ບ້ານ, ເມືອງ, ແຂວງ) and by post type/date; (2) capture comments, not just posts; (3) never re-open a link already processed.

## Decision

### 1. Keyword matching — match at capture, decide at store, filter at query

- Matching runs in the background service worker over post text + author name + comment text; never the URL.
- **Lao-aware matcher** (`src/lib/keywords.ts`): NFC-normalize both sides, case-fold Latin, **substring** `includes()` — never `\b` (Lao has no word boundaries, so ຂາຍດິນ must match both ຂາຍ and ດິນ). Diacritic combining marks are handled by NFC.
- Keyword config is a named `KeywordSet { include[], exclude[], min_hits }`, edited in the side panel and synced to the server.
- Each record is tagged `matched_keywords[]`, `match_score`, `matched_via`.
- **Store mode** (`matched` | `all`): default `matched` keeps LensDB a lead list; `all` keeps everything, still tagged, for retroactive keyword changes.
- "Post Date / page post / group post" are **fields, not keywords**: `created_at` (existing) and a new `container_type` (group | page | profile | feed | hashtag | search), both query filters.

### 2. Comments as a distinct record type

- `record_type: 'post' | 'comment'` with `parent_post_id` on comments; comments key on their own id.
- Facebook comments ride the already-intercepted `/api/graphql/` responses (`Comment` nodes); TikTok comments come from the already-allow-listed `/api/comment/list/`. So this is a parser addition, not a new interception path.
- A matching comment **promotes its parent post** into the lead set (`matched_via='comment'`) even if the post text didn't match.
- Privacy unchanged: author IDs hashed by default, post/comment content only, no per-person profiling.

### 3. Seen-link frontier

- New `seen_links` table (Dexie mirror + authoritative in LensDB), keyed on `url_hash = sha256(normalizeUrl(permalink))`.
- **URL normalization is the whole game** (`src/lib/url.ts`): strip `fbclid`/`__cft__`/utm/etc., lowercase host, upgrade http→https, drop fragment, canonicalize FB `permalink`↔`posts` and `story.php` identity params, stable query-param order. Two links to the same post must hash identically.
- States: `seen | queued | fetched | failed | skipped`; optional `refresh_after` TTL so price-changing posts can be re-checked while external listing links stay never-revisit.
- `seenCheck` / `seenMark` runtime messages; every captured permalink is auto-recorded as `seen`. Server `POST/GET /seen` reconcile across machines (`GET /seen?since=`).

## Consequences

Positive: LensDB becomes a keyword-scoped lead list; comments surface intent that photo-only posts hide; the frontier stops duplicate work and is the foundation for a future assisted-navigation mode. All matching is local and cheap (payload already in hand).

Negative / follow-ups: comments only exist in a payload when the operator expands the thread (full comment capture motivates assisted navigation, deferred); URL normalization must be maintained as Facebook changes link forms — it is unit-tested against real variants; Lao has no stemming, so keyword sets are exact-substring (acceptable for these terms).

## Alternatives considered

- **Filter at capture (drop non-matches immediately)** — rejected as the only mode: loses data for retroactive keyword changes. Offered as `store_mode='matched'` default, with `all` as the escape hatch.
- **Comments in the posts table** — rejected: different identity and lifecycle; `record_type` + `parent_post_id` keeps one queryable table without conflating the two.
- **Dedup only on post key** — insufficient: doesn't stop re-opening the same permalink/external link; the frontier is a separate concern from record dedup.
