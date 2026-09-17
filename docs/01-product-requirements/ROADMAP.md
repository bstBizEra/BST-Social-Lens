# BST Social Lens — Roadmap

Governed by ADR-0005 (platform era) and ADR-0001…0004 (capture era). Dates are not committed
here; sequencing and exit criteria are. Status snapshot: `docs/00-governance/STATUS.md`.

## Capture era (Phases 0–4)

| Phase | Release | Content | State |
|---|---|---|---|
| 0 | v0.1.0 | WXT MV3 scaffold, MAIN-world interceptor, Dexie store, export | done |
| 1 | v0.2.0 | lens-api + LensDB (PostgreSQL), ingest, upsert dedup, ADR-0001 | done |
| 2 | v0.3.0 | Lao-aware keyword sets, comment records, seen-link frontier, ADR-0002 | done |
| 3 | v0.4.0 | Autonomous auto-scroll with caps/jitter, ADR-0003 | done |
| — | v0.5.0 | Social Lens Console served by lens-api | done |
| 4 | v0.6.0 | Assisted navigation (Layer B, PR #7 — **A/B evidence pending**); `/mcp` adapter (lens-api 0.4.0, shipped); Layer C logged-out worker (PR #8 — **evidence hold**); ADR-0004 | closing |

## Platform era (Phases 5–9) — SLL-PROP-DATA-001

| Phase | Release | Contract | Headline deliverables | Exit criterion (summary) |
|---|---|---|---|---|
| 5 | v0.7.0 | 001B Capture & Provenance | `POST /raw`, raw captures on server, provenance hashes, retention ordering L0→L1, store mode `all` recommended | 100 % of L1 rows trace to a raw capture; retention never deletes referenced rows |
| 6 | v0.8.0 | 001C Extraction · 001D Geo | Signal/asset classification, property claims with evidence + confidence, price observations (LAK/FX), Lao admin geography from Lao Data Map, location precision, PostGIS | classification precision ≥ 0.85; price/area normalisation ≥ 90 %; precision on 100 % of location claims |
| 7 | v0.9.0 | 001E Entity Resolution | Listing clusters, scored entity matching with review band, market properties with derived stats, advertisers, **Portal workbench** replaces Console, per-user auth | link precision ≥ 0.9 / recall ≥ 0.8 on reviewed sample; every link explainable |
| 8 | **v1.0.0** | 001G Quality · 001H (publication) | DQ A–D, review roles + audit, immutable dataset versions (L3), contacts excluded, `OBSERVED_ASKING` labels | first published version ≥ 500 market properties, DQ ≥ B on ≥ 70 %, zero contacts/author hashes exported |
| 9 | v1.1.0 | 001H (Reference API) | `/v1/market/*` incl. `/nearby` (PostGIS), comparable Level D contract, BizProp+ `asset_market_reference` on their side | BizProp+ report renders provenance-linked comparables; no path to L0–L2; `/nearby` p95 < 300 ms |

Preconditions: 001A frozen (D1–D7) before Phase 5 code; 001F DDL only after 001B–001E; Gate 5
register reopened before Phase 8 publication.

## Parallel capture track

Layer B A/B → merge #7 → tag v0.6.0 · Layer C 50-target run → GO/NO-GO on #8 · weekly
`_live/` parser-health · TikTok comment normalisation · store distribution decision.

## Out of scope (this roadmap)

OpenSearch/vector search; logged-in headless capture of any kind; member-profile aggregation;
transaction-price data (Social Lens holds asking prices only); merging `MP-…` with BizProp+
`AST-…` ids.
