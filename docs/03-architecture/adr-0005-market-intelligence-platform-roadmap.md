# ADR-0005: Social Lens as a market-intelligence data platform — roadmap extension (Phases 5–9)

- Status: **Accepted** (2026-09-17; OP-Vily delegated the call — 001A frozen with D1–D7 as recommended, release cut in §5 confirmed, Portal replaces the Console with the Console kept read-only until the Portal ships in Phase 7)
- Date: 2026-09-17
- Deciders: OP-Vily
- SDLC gate: 3 (Architecture Design) → feeds gates 1–2 (PRD / decomposition) for each phase
- Extends: ADR-0001 (topology), ADR-0002 (keywords/comments/frontier), ADR-0003 (autonomous mode), ADR-0004 (capture layers)
- Governs: `SLL-PROP-DATA-001` (frozen parent) and children `001A…001H`
- Supersedes: the "Phase 4" wording in ADR-0001/ADR-0004 ("`/mcp` adapter", "assisted navigation") — both shipped; Phase 4 is closed by this ADR.

## 1. Context

Through v0.6.0 (PR #7, pending A/B evidence) and lens-api 0.4.0, BST Social Lens is a **capture
product**: a browser extension captures Facebook/TikTok posts and comments from the operator's
own session, normalises them to SocialRecord v1, and pushes them to LensDB through lens-api;
a Console and an MCP adapter read them. Its roadmap was written in capture terms — Phases 0–4:
scaffold, ingest tier, keyword/comment/frontier, autonomous scroll, assisted navigation + MCP.
All of that is on `main` or in evidence-gated PRs.

The business need that Social Lens exists for is not "posts". It is a defensible view of the
Lao real-estate market: what is for sale/rent, where, at what asking price, advertised by whom,
how often, and how that changes — so that BizProp+ (PropTech by BST) can use it as comparable
evidence. Two facts about Lao social listings force the design:

1. **The same land is advertised by several freelance agents, companies, and sometimes the
   owner, at different prices, on different days, in different groups.** A capture product
   sees 17 posts; the market sees one property with 17 observations from 6 advertisers.
2. **Almost nothing in a post is a verified fact.** Prices are asking prices; "owner" is a
   claim; a Google Maps pin is a social coordinate, not a cadastral parcel; area is whatever
   the poster typed.

The parent artefact `SLL-PROP-DATA-001` (frozen 2026-09-17) therefore repositions Social Lens
as an **independent market-observation and data-refinement platform** with five contracts
(C01 Capture, C02 Extraction, C03 Geo-Normalisation, C04 Entity Resolution, C05 Publication),
four data layers (L0 RAW → L1 OBSERVATION → L2 INTELLIGENCE → L3 PUBLISHED), and one
governing principle:

> Never deduplicate away market evidence. Deduplicate source records, cluster advertisements,
> resolve real-world property entities, and preserve every price/location claim with provenance.

`001A Domain & Data Boundary` fixes the vocabulary, ownership, invariants I1–I10 and the
BizProp+ boundary. This ADR records the decision to adopt that direction and lays out the
roadmap that delivers it, so the capture-era phases and the platform-era phases form one line.

### Constraints carried forward (unchanged)

- ADR-0004 capture policy: extension-first; Layer B click-only; Layer C logged-out only,
  evidence-gated. The platform work does not change how evidence is *acquired*.
- AGENTS.md §3.4 data minimisation: post/comment content + engagement, author ids hashed, no
  member-profile aggregation. 001A adds contact points as hashed/masked, access-restricted,
  never published.
- Single PostgreSQL system of record behind one FastAPI boundary (ADR-0001). PostGIS and
  `pg_trgm` are added; OpenSearch/vector search is **not** adopted in this roadmap.
- Governance: `main-protection` ruleset, parser-health CI, STATUS.md snapshots, evidence
  gates for anything that touches accounts or graduates a spike.

## 2. Decision

1. **Adopt `SLL-PROP-DATA-001` as the architecture of record** for everything after v0.6.0.
   Social Lens owns observed market evidence and publishes market intelligence; BizProp+
   owns assets, titles, inspections, valuations and comparables and consumes only L3 through
   a versioned Reference API. No cross-database access in either direction. IDs are never
   merged (`MP-…` ≠ `AST-…`).

2. **Extend the roadmap with Phases 5–9** (§4), one phase per contract family, each closing
   with a release, an evidence gate and a STATUS.md snapshot. Phase 4 is declared closed.

3. **Vocabulary and invariants from 001A are binding on code**: table, column, API and screen
   names use the 001A glossary; PRs that violate I1–I10 (e.g. delete evidence as
   "duplicates", store a claim as a fact, expose L0–L2 to BizProp+) are rejected in review,
   and the CI gate grows a schema-lint for the most mechanical rules (§4, Phase 6).

4. **Sequencing rule**: no DDL for L2/L3 before 001B–001E are frozen (001F is the first DDL
   unit). The extension keeps shipping capture improvements in parallel; the server tier
   evolves additively (new schemas), never by rewriting L1.

5. **Evidence-first continues**: each phase has a measurable exit criterion (§4) computed
   from real Lao data, and graduation decisions are machine-enforced where possible (as
   Layer C already is).

## 3. Roadmap overview

```
CAPTURE ERA (done / evidence-gated)                PLATFORM ERA (this ADR)
─────────────────────────────────────────────────  ───────────────────────────────────────────────────────
Phase 0  scaffold, interceptor, Dexie     v0.1     Phase 5  Capture & Provenance (L0 on server)      v0.7
Phase 1  lens-api + LensDB                v0.2     Phase 6  Property extraction + geo (L2 claims)     v0.8
Phase 2  keywords, comments, frontier     v0.3     Phase 7  Entity resolution + Portal workbench      v0.9
Phase 3  autonomous auto-scroll           v0.4     Phase 8  Quality, review, publication (L3)         v1.0
         Console                          v0.5     Phase 9  BizProp+ Reference API + comparables      v1.1
Phase 4  assisted navigation (#7), /mcp   v0.6     ──────  Layer C worker graduates when its gate says GO
```

Phases are sequential in their *contracts* (001B → … → 001H) but overlap in delivery: the
extension track (capture quality, parser health, store distribution) runs alongside.

## 4. Phases

### Phase 5 — Capture & Provenance Contract (001B) → release **v0.7.0**

Goal: L0 exists on the server; every L1 row can be traced to the evidence it came from.

| Deliverable | Detail |
|---|---|
| `POST /raw` | Extension ships raw payloads (hash, size-capped body, capture context) alongside `/ingest`; `raw_captures` table; `records.raw_capture_id` link (D1) |
| Provenance chain | `payload_hash` on every raw capture; `records.content_hash`; capture events table so a post captured twice keeps both captures (I1) |
| Retention ordering | L0 body ≤ 90 d → L1 ≥ 24 mo → L2/L3 never; publish-referenced rows exempt (D3); `LENS_RETENTION_DAYS` split into `LENS_RAW_RETENTION_DAYS` / `LENS_RECORD_RETENTION_DAYS` |
| Store mode | `all` becomes the recommended setting; keywords tag but no longer gate retention (D2, staged: default flips in Phase 6 when classification exists) |
| Frontier | `seen_links` gains `first_capture_id`; Layer C statuses unchanged |
| Governance | ADR-0005 accepted; 001A/001B frozen; STATUS.md snapshot |

Exit criterion: on a 7-day capture sample, 100 % of `records` rows resolve to a stored raw
capture; retention dry-run deletes nothing referenced by L1; extension sync latency unchanged
(± 10 %).

### Phase 6 — Property Extraction & Geo-Normalisation (001C, 001D) → release **v0.8.0**

Goal: observations, claims and locations exist as L2 data with confidence, never as facts.

| Deliverable | Detail |
|---|---|
| Classification | `observation` per source record: signal class (`PROPERTY_SALE / RENT / WANTED / AGENT_AD / DEVELOPER / PRICE_DISCUSSION / MARKET_INFO / NON_PROPERTY / UNCERTAIN`) and asset type (`LAND / HOUSE / … / UNKNOWN`); rule-based first (keyword groups: asset × intent × price/area/location), LLM-assisted second, both recorded with `extraction_method` + `confidence` |
| Claims | `property_claims` + `field_evidence`: price text/normalised (LAK, THB, USD → LAK with FX date), area text/normalised (`20x30` → 600 m², ຮາຍ/ha/m²), frontage/depth, transaction type, advertiser role, contact points (hashed/masked, restricted — D5); every claim carries evidence span, method, confidence, review status (I2) |
| Geo | `geo.provinces/districts/villages` loaded from BST Lao Data Map (versioned, D4); location resolver for village/district/province text, Google Maps URLs, lat/long → `location_claim` with **precision** (`EXACT_COORDINATE … TEXT_ONLY … UNKNOWN`) and confidence (I6); PostGIS enabled, `ST_Within` against admin polygons |
| Price observations | `price_observations` time-series (type, original amount/currency, LAK, FX, per-m² when area known) — append-only (I7, I4) |
| Extension | default `storeMode: 'all'`; keyword groups replace the flat list; on-page badge shows signal class |
| CI | schema-lint: no column named `price`/`owner`/`property` outside the 001A vocabulary; L1 tables not altered by L2 migrations |
| Governance | 001C/001D frozen; 001F (DDL) drafted from 001B–001D for the L2 subset |

Exit criterion: on ≥ 300 real observations, classification precision ≥ 0.85 on a hand-labelled
100-record sample; price and area normalisation correct on ≥ 90 % of records where the field
is present; location precision assigned on 100 % of location claims; zero claims stored
without confidence.

### Phase 7 — Entity Resolution & Portal Workbench (001E) → release **v0.9.0**

Goal: many observations → one market property, reversibly, with humans in the loop.

| Deliverable | Detail |
|---|---|
| Source dedup | Capture events merged into one source record (already true for `platform:post_id`; extended to permalink variants) |
| Listing clusters | Same-advertisement detection (text simhash, image perceptual hash, contact hash, price, location) → `listing_clusters`; records stay separate (I1) |
| Entity resolution | Scored matcher (geo proximity, area ±, price ±, contact overlap, image match, text similarity, village/district agreement, map agreement, temporal) → `entity_candidates` → `entity_decisions` (`HIGH_CONFIDENCE_MATCH ≥ 80 / REVIEW_REQUIRED 60–79 / SEPARATE_CANDIDATE < 60`), weights calibrated on Lao data and versioned; merge/split with `SUPERSEDED_BY` history (I8) |
| Market properties | `market_properties` with derived stats (min/max/median/latest asking, dispersion, first/last observed, observation count, advertiser count, resolution confidence) — derived, never stored as "the price" |
| Advertisers | `advertisers` (role) linking source authors; `advertiser_contacts` restricted |
| Portal | Social Lens Portal replaces the Console: Capture Inbox → Property Leads → Extraction Review → Map Review → Duplicate/Match Review → Market Property 360 → Advertiser → Price History → Data Quality. Same-origin on lens-api; per-user auth (short-lived tokens via MCP Hub, closing the ADR-0001 note) |
| `/mcp` | tools gain `search_market_properties`, `get_market_property`, `nearby` (L2, internal) |
| Governance | 001E frozen; 001F DDL for L2 complete |

Exit criterion: on a reviewed sample of ≥ 50 market properties, precision of HIGH_CONFIDENCE
links ≥ 0.9 and recall on known multi-agent cases ≥ 0.8; every link explainable from stored
signals; review queue drains ≤ 1 business day at expected volume.

### Phase 8 — Quality, Review & Publication (001G, 001H part 1) → release **v1.0.0**

Goal: a published, versioned market dataset that can be defended.

| Deliverable | Detail |
|---|---|
| Quality | DQ score (price 20 / area 15 / location 20 / coordinate 15 / post date 10 / evidence 10 / entity match 10 → A–D) separate from resolution confidence; validation rules; exception queue |
| Review | Reviewer roles, decision audit (`audit.events`), reversible overrides |
| Publication | `publish.datasets` / `dataset_versions`: immutable snapshots of L3 (market properties + observations behind them, contacts excluded, all prices `OBSERVED_ASKING`); publish is the only path out (I9) |
| Retention | publish-referenced rows exempt from purge (D3) enforced in DB |
| Governance | 001G frozen; 001H API schema frozen; v1.0.0 = first published dataset version; Gate 8/12 evidence recorded in STATUS.md |

Exit criterion: first dataset version published with ≥ 500 market properties, DQ ≥ B on ≥ 70 %,
zero contact points or author hashes in the export, full provenance from any published price to
its raw capture demonstrable in the Portal.

### Phase 9 — BizProp+ Reference API & comparables (001H part 2) → release **v1.1.0**

Goal: BizProp+ consumes Social Lens without coupling.

| Deliverable | Detail |
|---|---|
| Reference API v1 | `/v1/market/properties`, `/{id}`, `/observations`, `/price-observations`, `/comparables`, `/statistics`, `/nearby?lat&lon&radius_m&property_type&from` (PostGIS `ST_DWithin`); versioned; reads dataset versions only; per-consumer tokens (D6) |
| Comparable contract | Every record labelled comparable **Level D — observed asking price** (D7, I4); location precision and DQ grade in every response so BizProp+ can filter |
| BizProp+ side (their repo) | `asset_market_reference` (`SAME_PROPERTY / POSSIBLE_SAME_PROPERTY / COMPARABLE / NEARBY / REJECTED_MATCH`, score, method, verified_by/at); Social Lens never learns asset ids |
| Governance | 001H frozen; integration test suite shared with BizProp+; STATUS.md snapshot |

Exit criterion: BizProp+ valuation report renders a comparable set from Social Lens with
provenance links; no query path from BizProp+ reaches L0–L2; API p95 latency < 300 ms on
`/nearby` at 3 km radius over the published dataset.

### Parallel track — capture quality (no phase number)

Continues on the extension across all phases: A/B-gated Layer B merge and v0.6.0 tag; Layer C
graduation on GO; weekly `_live/` parser-health routine; TikTok comment normalisation; store
distribution (unlisted/enterprise) when the privacy disclosure is settled.

## 5. Release cut and versioning

| Release | Phase | Content gate |
|---|---|---|
| v0.6.0 | 4 | Layer B A/B evidence on #7 (pending) |
| v0.7.0 | 5 | `/raw` + provenance + retention ordering |
| v0.8.0 | 6 | classification, claims, geo, price observations; `storeMode: all` default |
| v0.9.0 | 7 | entity resolution, market properties, Portal |
| **v1.0.0** | 8 | first published dataset version |
| v1.1.0 | 9 | Reference API consumed by BizProp+ |

Extension and lens-api keep independent SemVer (`package.json` / FastAPI `version`); the
release tag is the repository release. lens-api's `/mcp` `serverInfo.version` follows lens-api.

## 6. Consequences

Positive
- Turns 17 posts into one market property with 17 provenance-backed observations — the
  actual product BizProp+ needs — without changing how evidence is acquired.
- Every number in a BizProp+ valuation traces to a raw capture; asking vs transaction, claimed
  vs verified, social coordinate vs parcel are structurally distinct, which is what makes the
  dataset defensible.
- Additive evolution: L1 and the extension contract are untouched; new schemas sit beside
  `records`; no big-bang migration.
- Governance already in place (ruleset, CI, STATUS.md, evidence gates) extends naturally;
  each phase has a measurable exit.

Negative / accepted
- Storage grows (raw payloads server-side; append-only claims and prices). Mitigated by
  retention ordering and hashing; measured in Phase 5.
- Entity resolution quality depends on calibration against Lao data that does not yet exist
  in volume; Phase 7 weights are starting points and must be re-tuned; the review queue is a
  real operational cost.
- Contact-point extraction is new personal data. It is restricted, hashed, masked, never
  published, and its lawful-basis reasoning lives in the parked Gate 5 register; reopening
  that register is a precondition for **publishing** (Phase 8), not for building L2.
- The Portal is a larger UI than the Console; it replaces rather than extends it.
- Lao Data Map becomes a hard dependency (D4); its versioning must be respected.

## 7. Alternatives considered

- **Keep Social Lens as a capture product and do entity resolution inside BizProp+** —
  rejected: couples the valuation system of record to unverified social evidence, duplicates
  every extraction/geo component in BizProp+, and makes "duplicate posts" a BizProp+ problem.
- **Destructive deduplication (one row per property, latest price wins)** — rejected: destroys
  the evidence that makes the dataset valuable (price dispersion, advertiser count, history)
  and cannot be undone.
- **Direct SQL access from BizProp+ to LensDB** — rejected: no boundary, no versioning, leaks
  L0–L2 and contact points, breaks I9.
- **OpenSearch / vector search now** — deferred: PostgreSQL 16 + PostGIS + `pg_trgm` covers the
  first controlled implementation; revisit if semantic/multimodal similarity at scale is needed
  for entity resolution.
- **Jump to DDL from the parent** — rejected by the parent itself: 001A–001E freeze semantics
  first so "duplicate property" is never encoded ambiguously.

## 8. Decisions taken at acceptance (2026-09-17)

- 001A frozen; D1–D7 adopted as recommended.
- Release cut in §5 confirmed as written (no folding of Phases 5+6).
- The Portal replaces the Console in Phase 7; until then the Console stays as a read-only view of L1.

## References

- `SLL-PROP-DATA-001-parent-v0.1.md`, `SLL-PROP-DATA-001A-domain-data-boundary-v0.1.md`,
  `SLL-PROP-DATA-001-foundation-notes.md`
- ADR-0001 … ADR-0004; `docs/00-governance/STATUS.md`
- PostGIS `ST_DWithin`, `ST_Within` (radius and containment queries, spatial-index aware)
