# SLL-PROP-DATA-001H — BizProp+ Publication Contract v0.1

- Parent: `SLL-PROP-DATA-001` (frozen) · Boundary: `001A` §7 (frozen; D6, D7) · Inputs: `001E` market properties, `001G` DQ grades and review states · Physical: `001F` `publish.*` · Roadmap: ADR-0005 Phase 8 (part 1: dataset versions) and Phase 9 (part 2: Reference API) → v1.0.0 / v1.1.0
- Status: **Draft for freeze** (2026-09-17). Part 1 freezes with the first `PUBLISHED` dataset version; part 2 with the shared integration suite (§9).
- Contract family: **C05 Publication** — *only controlled/published market intelligence crosses the boundary*
- Precondition note: **executing** a publish (setting a version `PUBLISHED`) requires the security/compliance register (Gate 5, parked at owner decision). This contract defines the mechanism and is drafted without legal content.

## 1. Purpose and scope

Define the one path out of Social Lens: how a **dataset version** is assembled from L2, what
it contains and excludes, how it is frozen and withdrawn, and how BizProp+ reads it through
**Social Lens Reference API v1** without ever touching L0–L2. Every published number is an
**observed asking price** (comparable Level D) and carries its location precision and DQ
grade so the consumer can filter rather than trust.

## 2. Dataset and version lifecycle (part 1)

| State | Meaning | Transition | Who |
|---|---|---|---|
| `DRAFT` | members being assembled by the publish service from L2 as of a `stats_version` + `admin_version` | `assemble` (re-runnable; replaces members while DRAFT) | `publisher` |
| `PUBLISHED` | immutable: members, snapshot ids, observation ids, checksum fixed; export file written; `records.protected = true` for every L1 row behind a member (D3) | `publish` (one transaction + audit row; separation of duties per 001G Q3) | `publisher` |
| `WITHDRAWN` | no longer served by the API; rows and export kept (never deleted); reason recorded | `withdraw` | `publisher` |

Version id: `YYYY.MM.n` per dataset (`lao-residential-market.2026.11.1`). A new version is a
new row and a new export; nothing in an earlier version changes. The API serves the latest
`PUBLISHED` version by default and any `PUBLISHED` version by explicit id.

## 3. Inclusion rules (assemble)

A market property is a member when **all** hold: `status = ACTIVE`; ≥ 1 current observation
whose decision is `HIGH_CONFIDENCE_MATCH` or `CONFIRMED` (`REVIEW_REQUIRED`-only properties
are excluded); property DQ grade ≥ **C** (target for v1.0: ≥ B on ≥ 70 % of members);
primary location precision ≥ `DISTRICT`; latest observation ≤ 24 months old; no open
exception of kind `entity` or `price` (001G §2). Included observations per member: current
observations that meet the same decision rule and whose own DQ ≥ D (all evidence behind the
statistics is exported, even weak observations, so the statistics are reproducible).

## 4. Export shape (what crosses; 001A §7)

| Block | Fields | Explicitly excluded |
|---|---|---|
| `dataset_version` | dataset id, version, published_at, admin_version, stats_version, dq_version, match_version, checksum, counts | — |
| `market_properties[]` | `market_property_id`, asset type, primary location at **stated precision** (codes; point only when precision ≥ `PARCEL_APPROXIMATE`, else centroid flagged `centroid`), DQ grade, resolution confidence, review state, first/last observed, statistics per price type (`min/max/median/latest asking LAK`, dispersion, per-m² median, n), observation count, advertiser count, listing-cluster count | anything named "price" as a single value; contact points; author hashes; text beyond what §4 lists |
| `observations[]` | `observation_id`, `market_property_id`, platform, post date, observed_at, signal class, asset type, price observations (`price_type`, original amount + currency, LAK amount, FX date/source, per-m²), area (m², confidence), location precision, advertiser **role**, DQ score, `first_payload_hash` (provenance anchor) | raw text, author name/hash, contact points, media URLs, permalink |
| `price_observations[]` | as embedded above, flat, all labelled `evidence_level = "D"` and `price_basis = "OBSERVED_ASKING"` | — |
| `advertiser_roles` | per observation: role only | advertiser ids, names, contacts |
| `geography` | admin codes used, version, precision vocabulary | polygons (BizProp+ has its own copy from Lao Data Map) |

Format: one JSONL file per block inside a versioned bundle, plus `manifest.json` (checksum
per file, row counts, schema version). The bundle is what the checksum in
`publish.dataset_versions` covers. A negative test runs on every publish: the bundle contains
no string matching a phone/LINE/WhatsApp pattern, no `author_hash`, no `contact_hash`, no
`raw_value` key (**hard stop**; the publish transaction rolls back).

## 5. Reference API v1 (part 2) — `/v1/market/*`

Separate router, separate per-consumer tokens (D6), reads **only** `publish.*` and the export
bundle (materialised into read-only tables per version), never `extract.*`/`geo.*`/`market.*`.

| Endpoint | Semantics |
|---|---|
| `GET /v1/market/versions` | published versions (id, published_at, counts) |
| `GET /v1/market/properties?district=&village=&asset_type=&min_dq=&version=&page=` | members of a version, filterable |
| `GET /v1/market/properties/{id}?version=` | property + statistics + its observations (+ `superseded_by` when the id was merged after that version) |
| `GET /v1/market/observations?property_id=&from=&to=&version=` | observations behind a property |
| `GET /v1/market/price-observations?property_id=&price_type=&version=` | flat time-series, `evidence_level = D` |
| `GET /v1/market/statistics?district=&asset_type=&price_type=&version=` | aggregate asking statistics over members |
| `GET /v1/market/comparables?asset_type=&area_sqm=&lat=&lon=&radius_m=&min_precision=&min_dq=&version=` | ranked comparables (distance, area similarity, recency); `min_precision` mandatory (001D §5) |
| `GET /v1/market/nearby?lat&lon&radius_m&asset_type&min_precision&version` | PostGIS `ST_DWithin` over members with precision ≥ `min_precision` (Haversine fallback tagged until G1) |

Every response carries `dataset_version`, `evidence_level: "D"`, `price_basis:
"OBSERVED_ASKING"`, per-item `location_precision` and `dq_grade`. Ids are Social Lens ids
only; BizProp+ keeps its `asset_market_reference` on its side (001A §7) and Social Lens
never receives asset ids. Rate limits and token scopes per consumer; p95 < 300 ms on
`/nearby` at 3 km over the published dataset (ADR-0005 exit criterion).

## 6. Provenance guarantee

From any published number: `price_observation` → `observation_id` → (Social Lens-internal)
claim with evidence span → capture event → `first_payload_hash` → raw capture row (body may be
purged; hash and context never). BizProp+ sees the anchor (`first_payload_hash`) but cannot
dereference it; Social Lens reviewers can (001G role). This is the "full provenance from any
published price to its raw capture" exit criterion.

## 7. Retention interaction (D3)

`publish` sets `records.protected = true` for every L1 row behind a member in one transaction
with the version row; `purge_records()` already excludes protected rows (Phase 5). Raw
**bodies** still age out (hash stays); withdrawal does **not** unset `protected` (evidence
behind a once-published number is kept).

## 8. Endpoints / MCP (internal)

`POST /admin/publish/assemble?dataset=&version=` · `POST /admin/publish/publish` (requires
`publisher`; refused while any open exception of kind `entity`/`price` exists among members,
or while the Gate 5 precondition flag is unset) · `POST /admin/publish/withdraw` ·
`GET /publish/versions` · `GET /publish/versions/{id}/manifest` · MCP `list_dataset_versions`
(read-only). No MCP tool can publish.

## 9. Acceptance gate (freeze criteria)

Part 1: first version `PUBLISHED` with ≥ 500 members, DQ ≥ B on ≥ 70 %, exclusion negative
test passing, checksum reproducible from L2 at the recorded `stats_version`/`admin_version`,
`protected` set for 100 % of member L1 rows, provenance walk demonstrated for 20 random
published prices.
Part 2: shared integration suite with BizProp+ green (`/nearby`, `/comparables`,
`/properties/{id}` against a published version); no query path from the v1 router reaches
L0–L2 (static check: router imports only the publish store); p95 latency met.

## 10. Decisions requested (defaults apply if not overruled, per delegation)

| ID | Question | Default |
|---|---|---|
| P1 | Members require decision `HIGH_CONFIDENCE_MATCH`/`CONFIRMED` and DQ ≥ C; `REVIEW_REQUIRED`-only properties never publish? | **Yes** |
| P2 | Export = JSONL bundle + manifest with per-file checksums; the API reads materialised per-version tables, never live L2? | **Yes** |
| P3 | Points published only at precision ≥ `PARCEL_APPROXIMATE`; coarser locations export codes + a flagged centroid? | **Yes** |
| P4 | Withdrawal keeps rows, export and `protected`; only API visibility changes? | **Yes** |
| P5 | The publish endpoint is hard-blocked by a `LENS_PUBLISH_GATE5_OK` flag that only the owner sets once the parked register is done? | **Yes** (mechanism only; content of the register out of scope) |

## 11. Out of scope

Legal/compliance register content (parked); BizProp+ side tables; Portal screens;
OpenSearch/vector search (deferred per ADR-0005).
