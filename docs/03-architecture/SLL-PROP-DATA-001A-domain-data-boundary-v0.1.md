# SLL-PROP-DATA-001A — Domain & Data Boundary v0.1

- Parent: `SLL-PROP-DATA-001` — Social Lens Property Market Intelligence Data Architecture v0.1 (frozen)
- Status: **Draft for freeze** (OP-Vily decides; §9 lists the decisions that block freeze)
- Date: 2026-09-17
- Scope: terminology, ownership, system-of-record boundaries, invariants, merge/link rules, and
  exactly what BizProp+ may consume. **No tables, no DDL** — that is 001F, after 001B–001E.
- Acceptance (from the parent): before any table is designed we can answer deterministically
  *what each entity means, which system owns it, what is evidence vs claim vs resolved entity,
  what may be merged, what must never be deleted through deduplication, and what BizProp+ is
  permitted to consume.*

## 1. Position statement

BST Social Lens is an **independent market-observation and data-refinement platform**. It
owns observed market evidence about Lao real estate and publishes controlled market
intelligence. It is not a scraping subsystem of BizProp+ (PropTech by BST), and BizProp+
never reads its tables. The governing principle, verbatim from the parent:

> Never deduplicate away market evidence. Deduplicate source records, cluster advertisements,
> resolve real-world property entities, and preserve every price/location claim with provenance.

## 2. Layers and what lives where

| Layer | Name | Meaning | Mutability | Today (v0.6 / lens-api 0.4) |
|---|---|---|---|---|
| **L0** | RAW | Captured evidence exactly as received: platform payload bodies, capture context, hashes | Immutable; append-only; retention by policy only | Extension IndexedDB `raw` (30-day purge, never sent to server) — **gap, see D1** |
| **L1** | OBSERVATION | Normalised source posts/comments (SocialRecord v1), seen frontier | Upsert on source identity; engagement refreshes; content never rewritten | LensDB `records`, `seen_links` — exists |
| **L2** | INTELLIGENCE | Classification, extracted claims, normalised claims, geo resolution, entity resolution, clusters, advertisers, price observations, quality/review | Append-only claims; decisions versioned; nothing in L1 modified | Does not exist |
| **L3** | PUBLISHED | Quality-controlled market reference dataset, versioned | Immutable per dataset version | Does not exist |

Only **L3** crosses to BizProp+ by default. L2 is Social Lens–internal (Portal, review).
L0/L1 are never exposed outside Social Lens.

## 3. Glossary (normative)

Each term has one meaning. Code, schemas and screens use these names.

| Term | Definition | Layer | Identity | Owner |
|---|---|---|---|---|
| **Raw Capture** | One captured payload (GraphQL/API body) with capture context (page URL, time, parser version, payload hash). Evidence, not data. | L0 | `raw_capture_id`; `payload_hash` for integrity | Social Lens |
| **Source Record** | One post or comment as the platform identifies it, normalised to SocialRecord v1. Today's `records` row. | L1 | `platform:post_id` (existing key) | Social Lens |
| **Source Author** | The platform account that posted, minimised to display name + `author_hash`. Not a person profile. | L1 | `author_hash` | Social Lens |
| **Container** | Where the source record was seen (group/page/hashtag/search). | L1 | `platform:container_id` | Social Lens |
| **Observation** | The statement "at time *t*, source record *S* presented property-related content". One per source record per capture; carries the classification outcome. | L2 | `observation_id` → one Source Record | Social Lens |
| **Extracted Claim** | A single field value asserted by an extractor from an observation, with evidence span, method and confidence (e.g. `price_text = "2.5 ຕື້"`). A claim is **never** a fact. | L2 | `claim_id` → Observation | Social Lens |
| **Normalised Claim** | The machine-usable form of a claim (e.g. `2 500 000 000 LAK`), still linked to the raw claim and its confidence. | L2 | same `claim_id`, normalised columns | Social Lens |
| **Location Claim** | A claim about place (village/district/province text, map URL, lat/long) with an explicit **precision** (`EXACT_COORDINATE … TEXT_ONLY … UNKNOWN`) and confidence. | L2 | `claim_id` | Social Lens |
| **Price Observation** | A time-stamped asking-price fact derived from claims: type (`ASKING_SALE`, `ASKING_RENT_MONTHLY`, …), original amount/currency, LAK amount + FX date, per-m² if area known. **Asking, never transaction.** | L2 | `price_observation_id` → Observation | Social Lens |
| **Advertiser** | The party advertising, as observed: role `OWNER_CLAIMED / FREELANCE_AGENT / COMPANY_AGENT / DEVELOPER / COMPANY / UNKNOWN`. `OWNER_CLAIMED` = the source said so. | L2 | `advertiser_id`; may aggregate several Source Authors | Social Lens |
| **Contact Point** | Phone/LINE/WhatsApp etc. extracted from text, stored normalised + hashed + masked; raw value access-controlled. | L2 (restricted) | `contact_hash` | Social Lens |
| **Listing Cluster** | Source records that are the *same advertisement* re-posted (same text/images/contact/price/location, different post). Records stay separate; the cluster links them. | L2 | `cluster_id` | Social Lens |
| **Market Property** | Social Lens's best belief that several observations refer to one real-world market asset. Not a land title, not a parcel, not a BizProp asset. | L2 | `market_property_id` (`MP-…`) | Social Lens |
| **Entity Decision** | A scored, versioned link Observation → Market Property (`HIGH_CONFIDENCE_MATCH / REVIEW_REQUIRED / SEPARATE_CANDIDATE`, plus human overrides). Reversible; history kept. | L2 | `decision_id` | Social Lens |
| **Quality Profile** | Field-coverage score (DQ A–D) for an observation or market property — separate from entity-resolution confidence. | L2 | attached to its subject | Social Lens |
| **Published Market Record** | A market property (with derived price statistics and the observations behind them) as included in a **dataset version**. | L3 | `market_property_id` + `dataset_version` | Social Lens |
| **Asset Market Reference** | BizProp+'s own link from its asset to a published market property: relationship, match score, who verified. | BizProp+ | `asset_id` × `market_property_id` | **BizProp+** |

Explicitly **not** entities in Social Lens: *Property* (ambiguous — use Market Property),
*Duplicate* (a relationship, not a thing — use source duplicate / listing cluster / entity
decision), *Owner* (use Advertiser with role), *Transaction price* (does not exist here).

## 4. Ownership and system-of-record boundaries

| Data | System of record | May read | May write |
|---|---|---|---|
| L0 raw evidence, L1 source records, frontier | Social Lens (LensDB) | Social Lens only | Extension via `/ingest` (L1), Layer C worker via `/ingest` + `/seen` (L1 refresh, logged-out public only) |
| L2 intelligence (claims, geo, entities, clusters, advertisers, contacts, prices, quality, review) | Social Lens | Social Lens Portal + BST agents via `/mcp` (read-only) | Social Lens workers + human review only |
| L3 published dataset versions | Social Lens | BizProp+ via versioned Reference API; BST agents | Social Lens publish step only |
| BizProp+ assets, titles, inspections, valuations, comparables, asset↔market references | **BizProp+** | BizProp+ | BizProp+ |
| Lao administrative geography (province/district/village, geometry) | **Shared reference** — sourced from BST Lao Data Map; Social Lens holds a versioned copy | both | neither (upstream) |

Rules: no cross-database SQL in either direction; no shared tables; IDs are never merged
(`MP-…` ≠ `AST-…`); geography is referenced by shared codes, not copied ad hoc.

## 5. Invariants (non-negotiable)

| # | Invariant | Consequence for design |
|---|---|---|
| I1 | **Evidence is never deleted by deduplication.** L0 and L1 rows are removed only by an explicit retention policy or a lawful deletion request, never because they were "duplicates". | Source-duplicate handling is *merge of capture events into one Source Record*, not deletion of posts. Listing clusters and entity decisions are links. |
| I2 | **A claim is not a fact.** Extraction output carries method, confidence, evidence span and review status; nothing downstream may read a claim as verified. | No column named `price`; only `price_observation` with type + confidence. Portal shows claim vs normalised vs reviewed. |
| I3 | **Source Post ≠ Listing ≠ Market Property ≠ BizProp Asset.** | Four identities, four tables/namespaces, links between them. |
| I4 | **Asking price ≠ transaction price.** Social Lens has asking prices only; BizProp+ classifies them as comparable evidence Level D. | Published API labels every price `OBSERVED_ASKING`. |
| I5 | **Claimed owner ≠ verified owner. Social coordinate ≠ cadastral parcel. Market property ≠ land title.** | Advertiser roles, location precision, and the `market_` prefix are mandatory vocabulary. |
| I6 | **Location precision and confidence are always explicit.** A village-level observation never masquerades as a parcel coordinate. | Every location claim has `precision`; radius queries must filter on it. |
| I7 | **Time is preserved.** Price and location observations are time-series; earlier values are never overwritten. | Price observations append; market property statistics are derived, never stored as "the price". |
| I8 | **Entity resolution is reversible and explainable.** Every link carries score, contributing signals, method version, and (if human) reviewer. Un-linking restores the prior state. | Decisions table with history; no destructive merge of observation rows. |
| I9 | **Only L3 crosses the boundary.** BizProp+ consumes published dataset versions through the Reference API; never L0–L2, never contact points, never raw text beyond what the dataset version includes. | Publish step is the only path out; contact points are excluded from L3. |
| I10 | **Data minimisation holds at every layer** (AGENTS.md §3.4, ADR-0004): post/comment content + engagement, author ids hashed, no member-profile aggregation. Contact points are the one addition and are restricted. | Advertiser is not a person dossier; contact points hashed/masked, raw value access-controlled, never published. |

## 6. What may be merged, linked, or must stay apart

| Situation | Action | Never |
|---|---|---|
| Same platform post captured twice (feed + permalink, two runs) | **Merge capture events** into one Source Record; keep both Raw Captures | Discard either capture |
| Same advertisement re-posted (same text/images/contact/price/location, new post id) | **Link** into one Listing Cluster; both Source Records and both Observations remain | Delete the re-post |
| Different agents/owner advertising the same land | **Link** each Observation to one Market Property via scored Entity Decisions; all price observations retained | Collapse to one price; delete "duplicates" |
| Two Market Properties later found to be one | **Merge decision**: one becomes canonical, the other `SUPERSEDED_BY` with history; observations re-pointed by new decisions | Delete the superseded id (BizProp+ may reference it) |
| One Market Property later found to be two | **Split decision**: new id, observations re-decided | Edit observations in place |
| Same advertiser under several platform accounts | **Link** Source Authors to one Advertiser (evidence: contact hash, name) | Merge author hashes |
| Conflicting area/location claims for one property | **Keep all claims**; canonical values are derived with a stated rule and confidence | Pick one silently |

## 7. BizProp+ consumption contract (boundary summary; full detail in 001H)

Permitted: published market properties and their derived statistics (min/max/median/latest
asking, dispersion, first/last observed, observation count, advertiser count, resolution
confidence, DQ grade), the published observations behind them (type, asking price, area,
location at its stated precision, post date, advertiser *role*), nearby/comparable queries,
dataset version metadata. All prices labelled **OBSERVED_ASKING**; comparable Level **D**.

Not permitted: L0/L1/L2 access, raw payloads, contact points, author hashes, unpublished or
`REVIEW_REQUIRED` entities, anything from a dataset version not marked published.

BizProp+ records its own `asset_market_reference` (`SAME_PROPERTY / POSSIBLE_SAME_PROPERTY /
COMPARABLE / NEARBY / REJECTED_MATCH`) on its side; Social Lens never learns BizProp+ asset ids.

## 8. Fit with what exists — tensions to resolve

| Current behaviour | Tension with this document | Proposed resolution (decision in §9) |
|---|---|---|
| `storeMode: 'matched'` (default) keeps only keyword hits **in the extension**; non-matching posts never reach L1 | Discards evidence at the source; a post that fails today's keywords may be the fourth observation of a market property | D2: default `storeMode: 'all'` once L2 classification exists; keywords become a *lead* signal, not a *retention* gate |
| Raw payloads stay in IndexedDB (30 days) and are never sent to the server | L0 is not on the server → no provenance chain from claims back to evidence | D1: add `POST /raw` (payload hash + body, size-capped) so L0 lives in LensDB; extension purge stays |
| `LENS_RETENTION_DAYS` = 730 on `records` (merged 2026-09-17) | Acceptable as *policy* retention (I1 allows policy), but L2 links to purged L1 rows would dangle | D3: retention applies to L0 body first, L1 last; L2/L3 never purge; purge is blocked for rows referenced by a published dataset |
| `records` is a single table mixing posts and comments | Fine as L1; L2 must not be bolted onto it | 001F: L2 in separate schemas (`extract`, `geo`, `market`, `resolution`, `quality`, `publish`) |
| Layer C worker refreshes engagement on L1 rows | Compatible (I7 says price/location are time-series; engagement refresh on L1 is source-level) | Engagement history becomes an L2 time-series if ever needed |
| `/mcp` tools read L1 directly | Fine for BST-internal agents; **not** the BizProp+ path | Reference API for BizProp+ is separate (001H); `/mcp` stays internal |

## 9. Decisions required to freeze 001A

| ID | Decision | Recommendation |
|---|---|---|
| D1 | Server-side L0: ship raw payloads to LensDB (`POST /raw`, hashed, size-capped, retention-governed)? | **Yes** — without it the provenance chain ends at L1 |
| D2 | Extension default `storeMode` → `all` when L2 classification exists (keywords = lead signal only)? | **Yes**, staged: keep `matched` until classification (001C) ships |
| D3 | Retention: L0 body ≤ 90 days, L1 ≥ 24 months, L2/L3 never; publish-referenced rows exempt? | **Yes** |
| D4 | Lao geography source of truth = BST Lao Data Map (versioned copy in LensDB)? | **Yes** |
| D5 | Contact points: extract and hash for entity resolution; raw value restricted to Portal reviewers; never published? | **Yes** |
| D6 | Name for the L3 consumer API: `Social Lens Reference API v1` at `/v1/market/*`, separate from `/mcp` and `/records`? | **Yes** |
| D7 | Comparable level for Social Lens evidence in BizProp+ = **D** (observed asking), fixed in 001H? | **Yes** |

## 10. Next controlled units (unchanged from the parent)

001B Capture & Provenance → 001C Property Extraction → 001D Geo-Normalisation → 001E
Entity Resolution → **001F Canonical Database Design** (first DDL) → 001G Quality & Review →
001H BizProp+ Publication. 001B can start immediately on D1/D3; 001F waits for 001B–001E.
