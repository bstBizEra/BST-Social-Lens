# SLL-PROP-DATA-001D — Geo-Normalisation Contract v0.1

- Parent: `SLL-PROP-DATA-001` (frozen) · Boundary: `001A` (frozen, D4) · Extraction: `001C` (location claims defined there) · Roadmap: ADR-0005 Phase 6 → v0.8.0
- Status: **Draft for freeze** (2026-09-17). Freezes together with 001C when the resolver passes §9. Implementation state: §2 reference copy (`app/schema_geo.sql`, GeoJSON held until PostGIS), §3 record, §4.2 text path, §4.1 point path without polygons (codes via §4.3 agreement only, signalled), §4.3 reconcile by centroid distance, §6 confidence, §7 endpoints/MCP — in `app/geo/` (`GEO_RULE_V1` 1.0.0). Pending: PostGIS `ST_Within` (G1), §9.2 golden locations (operator), §9.5 live evidence.
- Contract family: **C03 Geo-Normalisation** — *location precision and confidence must be explicit*

## 1. Purpose and scope

Resolve every `LOCATION_TEXT`, `MAP_URL` and `COORDINATE` claim (001C §5.2) to canonical Lao
administrative geography and, where available, a point — with an explicit **precision** and
**confidence** on every result (I6), without ever presenting a social-media location as a
cadastral parcel (I5).

In scope: the administrative reference copy (D4), the resolver, precision/confidence
semantics, coordinate handling, the resolved-location record, spatial queries the platform
may run, and the acceptance gate. Out of scope: parcel/title geometry (BizProp+ owns it),
entity resolution (001E uses these outputs), DDL (001F: `geo.*`).

## 2. Administrative reference (D4)

| Aspect | Rule |
|---|---|
| Source of truth | **BST Lao Data Map** (province → district → village; Lao + English names; geometry). Social Lens never edits it |
| Copy in LensDB | `geo.admin_versions` (`version, imported_at, source_ref, checksum`) + `geo.provinces`, `geo.districts`, `geo.villages` (`code, name_lo, name_en, name_variants[], parent_code, geom (MultiPolygon, SRID 4326), centroid`) — one import per version, immutable; a new version is a new set of rows, older versions retained because resolved locations reference `admin_version` |
| Codes | Lao Data Map codes as-is (`P-xx`, `D-xxyy`, `V-xxyyzzz` or whatever the upstream scheme is — never re-keyed). Social Lens adds nothing but `name_variants` (spelling/transliteration aliases collected from review, versioned separately in `geo.name_aliases` so upstream stays pristine) |
| Fallback when no village geometry | district polygon + village centroid if provided; precision cannot exceed `VILLAGE` and confidence is capped at 0.7 |
| Vientiane Capital | modelled as a province (`ນະຄອນຫຼວງວຽງຈັນ`) — resolver treats `ນະຄອນຫຼວງ` as a province-level term |

## 3. Resolved location record (logical)

| Field | Meaning |
|---|---|
| `resolved_location_id` | serial |
| `claim_id` / `observation_id` | the 001C claim(s) this resolution is derived from (several claims of one observation may combine into one resolution; each contributing claim listed in `inputs[]`) |
| `admin_version` | the `geo.admin_versions` row used |
| `province_code, district_code, village_code` | nullable, filled to the deepest level resolved |
| `lat, lng, geom (Point 4326)` | nullable; source in `point_source` |
| `precision` | `EXACT_COORDINATE · PARCEL_APPROXIMATE · VILLAGE · DISTRICT · PROVINCE · TEXT_ONLY · UNKNOWN` |
| `point_source` | `MAP_URL · TEXT_COORDINATE · VILLAGE_CENTROID · DISTRICT_CENTROID · PROVINCE_CENTROID · NONE` |
| `confidence` | `[0,1]`, §6 |
| `resolver_method, resolver_version` | `GEO_RULE_V1` + semver; `HUMAN` for Portal corrections (new row, `supersedes_id`) |
| `signals[]` | which matches fired (`village_exact_lo`, `district_alias_en`, `point_within_district`, `conflict_text_vs_point`, …) |
| `review_status` | `UNREVIEWED · LOW_CONFIDENCE · CONFIRMED · CORRECTED · REJECTED` |

One observation may end with several resolved locations (e.g. text says two villages);
the **primary** is the highest-confidence one, marked `is_primary`, and analytics use only
primaries unless asked otherwise. Nothing is overwritten (I7); a re-resolve appends.

## 4. Resolver `GEO_RULE_V1`

```
inputs: LOCATION_TEXT claims, MAP_URL claims (with parsed coords), COORDINATE claims
   │
   ├─ A. point path      MAP_URL/COORDINATE → point → ST_Within(point, admin polygons) → codes
   ├─ B. text path       normalise → gazetteer match (village ⊂ district ⊂ province) → codes (+ centroid)
   └─ C. reconcile       A ∧ B agree → precision from A, confidence ↑ ; disagree → keep both, flag conflict, primary = A if within Laos
```

### 4.1 Point path (A)

1. Parse coordinates from `MAP_URL` (`@lat,lng`, `q=lat,lng`, `ll=`, `!3d<lat>!4d<lng>`,
   `place/…/@lat,lng`) or from a `COORDINATE` claim. Short links (`goo.gl/maps`,
   `maps.app.goo.gl`) are **not** expanded by the resolver (no outbound fetch from the
   server tier — ADR-0004 boundary); they yield `precision = TEXT_ONLY`, `point_source =
   NONE`, `signals += ['short_map_link_unexpanded']` until an operator-side expander
   (extension, Layer B) records the expanded URL as a new claim.
2. Validate: inside the Laos bounding box (13.9–22.6 N, 100.0–107.8 E) and inside any
   province polygon; otherwise `precision = UNKNOWN`, `signals += ['point_outside_laos']`.
3. `ST_Within` against village → district → province polygons of the current
   `admin_version`; codes filled to the deepest containing level.
4. Precision: a URL with a place pin or a `COORDINATE` claim ⇒ `EXACT_COORDINATE`; a URL
   that is only a map viewport (`@lat,lng,zoom` with zoom < 15 and no place) ⇒
   `PARCEL_APPROXIMATE` if zoom ≥ 12 else `VILLAGE`-level with the point kept.

`EXACT_COORDINATE` means *the advertiser pinned this point*, nothing more — never a
surveyed parcel (I5). The word "parcel" in `PARCEL_APPROXIMATE` denotes scale (~100 m),
not cadastral status.

### 4.2 Text path (B)

1. Normalise: NFC, strip tone marks only for matching (`ບ້ານ` prefixes and suffix particles
   removed: `ບ້ານ, ບ., ບ້ານ​`, `ເມືອງ, ມ.`, `ແຂວງ, ຂ.`), Thai/English aliases lower-cased.
2. Gazetteer match in order: exact `name_lo` → exact `name_en` → `name_variants`/aliases →
   trigram similarity (`pg_trgm`, threshold 0.6, Lao-safe because it is character-based).
3. Hierarchy consistency: a village match is accepted only if its district/province either
   match other claims on the observation or are absent; a conflicting hierarchy lowers
   confidence and records `hierarchy_conflict`.
4. Ambiguity: villages with the same name in several districts (common) resolve only when a
   district or province claim disambiguates; otherwise `precision = DISTRICT`/`PROVINCE`
   (deepest unambiguous level) with candidates listed in `signals`.
5. Point: centroid of the deepest resolved polygon, `point_source = *_CENTROID`. A centroid
   is never labelled better than its polygon's level.

### 4.3 Reconcile (C)

| A (point) | B (text) | Result |
|---|---|---|
| point within B's deepest polygon | any | precision from A, `confidence = min(1, confA + 0.10)`, `signals += ['point_text_agree']` |
| point in a different district than B | — | two resolved rows; primary = A (`precision` from A, `confidence −0.15`), B kept with `conflict_text_vs_point`; `review_status = LOW_CONFIDENCE` on both |
| no point | B | precision = B's level, point = centroid |
| point outside Laos | B | primary = B; A row kept as `UNKNOWN` |
| neither | — | `precision = TEXT_ONLY` if any location text exists, else no row (observation has no location; that is a valid state) |

## 5. Precision semantics (normative — what a consumer may assume)

| Precision | May assume | Must not assume | Radius queries |
|---|---|---|---|
| `EXACT_COORDINATE` | advertiser-pinned point, ~±25 m | parcel boundary, ownership | yes |
| `PARCEL_APPROXIMATE` | point within ~100 m | exact pin | yes (r ≥ 150 m) |
| `VILLAGE` | inside the village polygon; point = centroid | any within-village position | only with `r ≥ village radius` or polygon queries |
| `DISTRICT` | inside the district | village | polygon queries only |
| `PROVINCE` | inside the province | district | polygon queries only |
| `TEXT_ONLY` | free text exists | anything geographic | excluded |
| `UNKNOWN` | nothing | anything | excluded |

Every spatial query in Social Lens (`/nearby` in 001H, entity resolution in 001E) **must**
filter on precision and state which levels it used. `ST_DWithin` results without a
precision filter are a contract violation caught by review (and by the query helper, which
requires an explicit `min_precision` argument).

## 6. Confidence

`confidence = base(precision path) × agreement`, with base: point from URL/coords 0.90;
village exact 0.85; village alias 0.75; village trigram 0.60; district exact 0.80; province
exact 0.80; fallback centroid inherits its level's base; `TEXT_ONLY` 0.30. `agreement`:
1.0 default, 1.1 on point/text agreement (capped 1.0), 0.8 on hierarchy conflict, 0.7 on
point/text district conflict. `< 0.5` ⇒ `review_status = LOW_CONFIDENCE`. Stored on every
row; a row without confidence is rejected (I6).

## 7. Run model and API (lens-api 0.6.0, with 001C)

| Surface | Semantics |
|---|---|
| In-run | the extraction run (001C §8) calls the resolver for each observation's location claims; geo rows carry the same `run_id` |
| `POST /admin/geo/import` | bearer; import a Lao Data Map export (GeoJSON/CSV, file path on the server or multipart) as a new `admin_version`; checksum recorded; never replaces an existing version |
| `POST /admin/geo/resolve?since=&force=` | bearer; re-resolve (new rows) — used after a new admin version or resolver version |
| `GET /observations/{key}` (001C) | includes `locations[]` (resolved rows, primary first) |
| `GET /geo/stats` | precision distribution, share with a point, unresolved text share, conflicts, current `admin_version` |
| `/mcp` | read-only `geo_stats`, `resolve_text` (dry-run resolver over a text, no write) |
| Never | outbound geocoding calls from the server (no Google/OSM APIs in this phase — a later ADR if ever; the Laos gazetteer is local) |

## 8. Invariants honoured / enforced

I5 (vocabulary: `EXACT_COORDINATE` ≠ parcel; no `parcel_id` anywhere in Social Lens), I6
(precision + confidence NOT NULL; query helper requires `min_precision`), I7 (re-resolution
appends; `admin_version` pins the geography used), I8 (signals + resolver version make each
result explainable; `HUMAN` corrections supersede, never overwrite), I9 (geo endpoints
internal; only 001H exposes locations at their stated precision), I10 (no new personal data;
map links are content the advertiser posted).

## 9. Acceptance gate (freeze criteria)

1. **Gazetteer fixture** — `services/lens-api/tests/fixtures/geo/admin-sample.geojson`: a
   small versioned subset of Lao Data Map (Vientiane Capital + 2 provinces, all districts,
   ≥ 200 villages) sufficient for tests; the full import is an operator step.
2. **Golden locations** — `fixtures/geo/golden-v1.jsonl`: ≥ 100 location strings/URLs from
   real posts (text only) hand-labelled with expected codes and precision. CI asserts:
   precision assigned on **100 %**; district-or-better correct on ≥ 85 % where labelled;
   village correct on ≥ 70 % where labelled; zero rows with `precision = EXACT_COORDINATE`
   without a point; every URL fixture with embedded coordinates yields a point.
3. **Determinism** — two runs on the fixture are byte-identical.
4. **Schema-lint** — no `parcel`, `title`, `owner` columns in `geo.*`; no writes to L1.
5. **Live** (STATUS.md): on the Phase 6 300-observation sample, `GET /geo/stats` shows
   100 % precision assignment on location claims, and the share of `TEXT_ONLY` +
   `UNKNOWN` is reported (target < 40 % — Lao posts often name only a village).

## 10. Decisions requested (defaults apply if not overruled, per delegation)

| ID | Question | Default |
|---|---|---|
| G1 | PostGIS enabled on LensDB in Phase 6 (extension `postgis`, `pg_trgm`) — installed on the bizera-wsl local cluster before the 001F migration? | **Yes**; runbook §2a gains an install step |
| G2 | Short map links are not expanded server-side; expansion is an extension-side (Layer B) capture concern? | **Yes** (no outbound fetch from lens-api) |
| G3 | Name aliases collected from review live in `geo.name_aliases` (Social Lens-owned), never patched into the imported Lao Data Map rows? | **Yes** |
| G4 | Vientiane Capital modelled as a province row; `ນະຄອນຫຼວງ` treated as a province term? | **Yes** |
| G5 | No external geocoding API in Phase 6 (local gazetteer only)? | **Yes**; revisit only via ADR |

## 11. Out of scope (next units)

001E entity resolution (uses precision-filtered proximity as one signal), 001F DDL
(`geo.*`), 001G reviewer corrections UI, 001H `/v1/market/nearby`.
