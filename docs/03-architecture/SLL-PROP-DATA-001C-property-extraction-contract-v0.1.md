# SLL-PROP-DATA-001C — Property Extraction Contract v0.1

- Parent: `SLL-PROP-DATA-001` (frozen) · Boundary: `001A` (frozen) · Capture: `001B` (frozen) · Roadmap: ADR-0005 Phase 6 → v0.8.0
- Status: **Draft for freeze** (2026-09-17). Freezes with the first extraction PR that passes the golden-fixture gate (§10). Implementation state: §4.3 + §5 + §6 + §7 tier 1 implemented in `services/lens-api/app/extract/` (RULE_V1 1.0.0, synthetic tests); §8 API/persistence pending 001F.
- Contract family: **C02 Property Extraction** — *AI/extraction output is a claim, not automatically a fact*
- Companion: `001D` Geo-Normalisation (location claims are defined here, resolved there)

## 1. Purpose and scope

Turn L1 source records into L2 **observations** and **claims** with explicit method,
confidence, evidence span and review status — so that price, area, transaction type,
asset type, advertiser role and contact points exist as *asserted values with provenance*,
never as facts, and nothing in L0/L1 is modified (001A §2, I2).

In scope: classification (signal class, asset type), field extraction, normalisation
(currency → LAK, area → m²), price observations, advertiser role, contact points, the
extraction run model, versioning, review states, and the acceptance gate.
Out of scope: geography resolution (001D), clustering/entity resolution (001E), DDL (001F —
this document defines *logical* records; physical tables follow in `extract.*`), review UI
(001G), publication (001H).

## 2. Lifecycle

```
L1 records (SocialRecord v1)
   │  extraction run r (method set M, version v)
   ▼
observation  ─── 1 per (record_key, run)  ── signal_class, asset_type, confidence
   │
   ├─► claim[]  ─── 1 per (observation, field, evidence span) ── text, method, confidence, review
   │       └─► normalised columns on the same claim (amount_lak, area_sqm, …)
   ├─► price_observation[]  ─── derived from PRICE claims, append-only time-series
   ├─► location_claim[]     ─── defined here, resolved in 001D (precision + confidence)
   └─► contact_point[]      ─── hashed/masked; raw value restricted (D5)
```

Rules: a run never updates an earlier run's rows; a re-run on the same record produces a
new observation with a higher `run_id`; the **current** observation for a record is the one
with the highest `run_id` whose `method_version` is not retired. Nothing is deleted.

## 3. Identities and records (logical)

| Record | Identity | Required fields | Notes |
|---|---|---|---|
| **Extraction Run** | `run_id` (serial) | `started_at, finished_at, method_set, method_version, records_in, observations_out, trigger (`scheduled`/`admin`/`backfill`)` | One row per invocation; the audit anchor for I8-style explainability |
| **Observation** | `observation_id` → (`record_key`, `run_id`) unique | `record_key, run_id, content_hash (copied from L1), signal_class, signal_confidence, asset_type, asset_confidence, extraction_method, observed_at (= record captured_at), post_date` | `content_hash` ties the observation to the exact L1 content it saw; if L1 content changes (new `content_hash`), a new run is due |
| **Claim** | `claim_id` → `observation_id` | `field, value_text, evidence_span, extraction_method, method_version, confidence, review_status, normalised_*` | One claim per field occurrence; several claims for the same field are allowed and kept (I7, 001A §6 "keep all claims") |
| **Price Observation** | `price_observation_id` → `observation_id` | `price_type, amount_original, currency_original, amount_lak, fx_rate, fx_rate_date, fx_source, price_per_sqm?, observed_at, post_date, confidence, claim_id` | Append-only (I4, I7). `price_per_sqm` only when an AREA claim with `confidence ≥ 0.6` exists on the same observation |
| **Location Claim** | `claim_id` with `field ∈ {LOCATION_TEXT, MAP_URL, COORDINATE}` | as Claim + `precision, resolved_* (001D)` | Extraction assigns raw text/URL/coords; 001D assigns precision and admin codes |
| **Contact Point** | `contact_hash` (sha256 of normalised value + server salt) | `kind (PHONE/LINE/WHATSAPP/FB_MESSENGER/OTHER), masked_value, raw_value (restricted column), first_seen, last_seen` | Never leaves Social Lens (I9, I10); raw value readable only by Portal reviewer role (001G) |

Field vocabulary (`claim.field`): `PRICE, AREA, FRONTAGE, DEPTH, TRANSACTION_TYPE,
ASSET_TYPE, ADVERTISER_ROLE, LOCATION_TEXT, MAP_URL, COORDINATE, CONTACT, LAND_TITLE_MENTION,
ROAD_ACCESS, PROJECT_NAME`. Fields outside this list require a contract revision.

## 4. Classification taxonomy

### 4.1 Signal class (exactly one per observation)

| Class | Definition | Minimum evidence |
|---|---|---|
| `PROPERTY_SALE` | Offers a property for sale | asset term + sale intent (`ຂາຍ`, "for sale", "sell") |
| `PROPERTY_RENT` | Offers a property for rent | asset term + rent intent (`ເຊົ່າ`, `ໃຫ້ເຊົ່າ`, "rent") |
| `PROPERTY_WANTED` | Seeks to buy/rent | asset term + want intent (`ຊື້`, `ຕ້ອງການ`, `ຊອກ`, "looking for", "wanted") |
| `AGENT_ADVERTISEMENT` | Agent/brokerage self-promotion without a specific asset | agent term (`ນາຍໜ້າ`, "agent", "ບໍລິການ") and no price/area/location claim |
| `DEVELOPER_PROJECT` | Project/development marketing | project terms (`ໂຄງການ`, "project", "phase", "unit") |
| `PRICE_DISCUSSION` | Talks about prices without offering | price term, no intent term |
| `MARKET_INFORMATION` | News, regulation, statistics | market/news terms, no intent |
| `NON_PROPERTY` | No property signal | no asset term and no price/area/location term |
| `UNCERTAIN` | Rules conflict or confidence < 0.5 | default when nothing above is clear |

`UNCERTAIN` and `NON_PROPERTY` are valid, expected outcomes. Extraction must never be
forced to a positive class (foundation notes: "UNKNOWN is safer than hallucinated data").

### 4.2 Asset type (exactly one per observation)

`LAND, HOUSE, APARTMENT, COMMERCIAL, WAREHOUSE, HOTEL, FARM, DEVELOPMENT_LAND, BUILDING,
OTHER, UNKNOWN`. Assigned from asset-term groups (`ດິນ/ທີ່ດິນ` → LAND, `ເຮືອນ/ບ້ານ+ຂາຍ` →
HOUSE, `ອາພາດເມັນ/ຄອນໂດ/ຫ້ອງ` → APARTMENT, `ຮ້ານ/ຕຶກແຖວ/ອາຄານພານິດ` → COMMERCIAL, `ສາງ` →
WAREHOUSE, `ໂຮງແຮມ/ເກດເຮົ້າ` → HOTEL, `ສວນ/ໄຮ່/ນາ` → FARM, `ໂຄງການ + ດິນ` → DEVELOPMENT_LAND,
`ຕຶກ/ອາຄານ` → BUILDING). Multiple groups → highest-scoring; tie → `UNKNOWN` with both
candidates recorded in `signals`.

### 4.3 Keyword groups (replace the flat list — D2 staged)

| Group | Role in rules | Seed terms (Lao / Thai / English; NFC, substring match per ADR-0002) |
|---|---|---|
| `asset` | classification | `ດິນ, ທີ່ດິນ, ເຮືອນ, ບ້ານ, ອາຄານ, ຕຶກ, ຫ້ອງ, ຄອນໂດ, ອາພາດເມັນ, ສາງ, ຮ້ານ, ສວນ, ไร่, ที่ดิน, บ้าน, land, house, condo, shophouse` |
| `intent_sale` | classification | `ຂາຍ, ຂາຍດ່ວນ, ขาย, sale, sell` |
| `intent_rent` | classification | `ເຊົ່າ, ໃຫ້ເຊົ່າ, เช่า, rent, lease` |
| `intent_want` | classification | `ຊື້, ຕ້ອງການ, ຊອກ, ຮັບຊື້, ต้องการ, wanted, looking for` |
| `price` | extraction lead | `ລາຄາ, ກີບ, ບາດ, ໂດລາ, ຕື້, ລ້ານ, ແສນ, USD, $, ฿, ₭, LAK, THB` |
| `area` | extraction lead | `ເນື້ອທີ່, ຕາແມັດ, ຕລມ, ເຮັກຕາ, ໄຮ່, ງານ, ຕາວາ, m2, m², sqm, ha, rai, x, ×` |
| `location` | extraction lead | `ບ້ານ, ເມືອງ, ແຂວງ, ນະຄອນຫຼວງ, ຖະໜົນ, ຮ່ອມ, ຕິດ, ໃກ້, location, google map, map, lat, long` |
| `agent` | classification | `ນາຍໜ້າ, ບໍລິການ, agent, broker, ຕົວແທນ` |
| `owner` | advertiser role | `ເຈົ້າຂອງ, ເຈົ້າຂອງຂາຍເອງ, ບໍ່ຜ່ານນາຍໜ້າ, owner, direct owner` |
| `project` | classification | `ໂຄງການ, project, phase, ເຟສ, unit, ຫຼັງ` |
| `title` | claim lead | `ໃບຕາດິນ, ໃບຕາດິນແທ້, ໂສມ, title deed, ນສ3, ໂສມແດງ` |
| `contact` | extraction lead | `ໂທ, ຕິດຕໍ່, tel, call, WhatsApp, LINE, ວັອດແອັບ, ໄລ` |

The extension keeps this structure in `src/lib/keywords.ts` (`KeywordGroups`), used for
the on-page badge and lead tagging only; retention gating ends when this contract ships
(D2: default `storeMode: 'all'`). The server holds the authoritative copy for extraction;
both are versioned by `keyword_groups_version` and must match in CI (§10.4).

## 5. Rule engine v1 (method `RULE_V1`)

Deterministic, pure, unit-testable, no network. Runs over `NFC(text)` of the L1 record
(post or comment) plus author display name for role signals only.

### 5.1 Classification decision table

```
has(asset)  has(sale)  has(rent)  has(want)  has(price|area|location)  has(agent)  has(project)  → class            base conf
   1           1          0          0             *                      *            *          PROPERTY_SALE      0.80
   1           0          1          0             *                      *            *          PROPERTY_RENT      0.80
   1           0          0          1             *                      *            *          PROPERTY_WANTED    0.75
   1           1          1          0             *                      *            *          (sale ∧ rent) → PROPERTY_SALE 0.60, signal "both_intents"
   1           0          0          0             1                      *            1          DEVELOPER_PROJECT  0.65
   1           0          0          0             1                      0            0          PRICE_DISCUSSION   0.55
   0           *          *          *             0                      1            *          AGENT_ADVERTISEMENT 0.60
   0           *          *          *             1                      *            *          UNCERTAIN          0.40
   0           0          0          0             0                      0            0          NON_PROPERTY       0.90
   otherwise                                                                                      UNCERTAIN          0.40
```

Adjustments (each recorded in `signals[]`): `+0.10` when a PRICE claim normalises; `+0.05`
per additional lead group present (max `+0.15`); `−0.15` when `exclude` terms hit (e.g.
`ລົດ` car, `ໂທລະສັບ` phone, `ວຽກ` job); `−0.10` for record_type `comment` (comments are
weaker evidence of an offer). Final confidence is clamped to `[0, 1]`; `< 0.5` ⇒ `UNCERTAIN`.

### 5.2 Field extractors

| Field | Pattern family | Output |
|---|---|---|
| `PRICE` | `⟨number⟩ ⟨lao_magnitude⟩? ⟨currency⟩?` and `⟨currency⟩ ⟨number⟩`; magnitudes `ຕື້`=10⁹, `ລ້ານ`=10⁶, `ແສນ`=10⁵, `ໝື່ນ`=10⁴, `ພັນ`=10³; Thai `ล้าน`=10⁶; numbers accept `2.5`, `2,500`, `2 500`, Lao digits `໐-໙` | `value_text`, `amount_original`, `currency_original ∈ {LAK, THB, USD, UNKNOWN}`, `price_basis ∈ {TOTAL, PER_SQM, PER_MONTH, PER_YEAR, UNKNOWN}` |
| `AREA` | `⟨n⟩ x ⟨m⟩` (metres; frontage × depth), `⟨n⟩ ⟨unit⟩` with `ຕາແມັດ/ຕລມ/m2/m²/sqm`=1, `ເຮັກຕາ/ha`=10 000, `ໄຮ່/rai`=1 600, `ງານ/ngan`=400, `ຕາວາ/ຕລວ/wa`=4 | `value_text`, `area_sqm`, `area_unit_original`, plus `FRONTAGE`/`DEPTH` claims when from `n x m` |
| `TRANSACTION_TYPE` | intent group | `SALE, RENT, WANTED_BUY, WANTED_RENT, UNKNOWN` |
| `ADVERTISER_ROLE` | owner/agent/project groups + author-name hints (`Property`, `Real Estate`, `ອະສັງຫາ`) | `OWNER_CLAIMED, FREELANCE_AGENT, COMPANY_AGENT, DEVELOPER, COMPANY, UNKNOWN` (001A vocabulary; `OWNER_CLAIMED` only when the text says so) |
| `LOCATION_TEXT` | `ບ້ານ ⟨name⟩`, `ເມືອງ ⟨name⟩`, `ແຂວງ ⟨name⟩`, `ນະຄອນຫຼວງວຽງຈັນ`, Thai/English equivalents | one claim per admin-level mention, raw text kept; resolution in 001D |
| `MAP_URL` | `maps.google.*`, `goo.gl/maps`, `maps.app.goo.gl`, `google.com/maps` | URL; coordinates parsed when present in the URL (`@lat,lng`, `q=lat,lng`, `!3d…!4d…`) → also a `COORDINATE` claim |
| `COORDINATE` | `⟨lat⟩, ⟨lng⟩` decimal pairs within Laos bbox (13.9–22.6 N, 100.0–107.8 E) | `lat, lng` |
| `CONTACT` | Lao mobile `020 5xxx xxxx / 020 2xxx / 020 9xxx` (8 digits after 020), landline `021/030/031…`, `+856`, LINE/WhatsApp ids | `kind, masked_value (020 5XXX XX12), contact_hash`; raw value in restricted column only |
| `LAND_TITLE_MENTION` | title group | `value_text` only (never a verified title — I5) |

Every claim stores `evidence_span = {start, end}` as UTF-16 code-unit offsets into the
NFC-normalised text (the same normalisation the extension uses), so the Portal can highlight
it without re-running extraction.

### 5.3 Confidence per claim

`confidence = pattern_base × context_factor`, where `pattern_base` is fixed per pattern
(explicit unit + currency 0.90; magnitude word without currency 0.75; bare number near a
price term 0.50; area from `n x m` 0.85; from unit 0.90; contact regex 0.95; location text
0.70; map URL 0.95; coordinate 0.90) and `context_factor` is 1.0, or 0.8 when two competing
claims of the same field conflict by more than 20 %. Claims with `confidence < 0.3` are
still stored (evidence of ambiguity) but flagged `review_status = 'LOW_CONFIDENCE'`.

## 6. Normalisation

| Aspect | Rule |
|---|---|
| Currency → LAK | Source of FX is a `fx_rates` reference (date, currency, lak_per_unit, source ∈ {`BOL_REFERENCE`, `MANUAL`}). Rate at **post date** (fallback: capture date; then nearest earlier rate ≤ 7 days; else no `amount_lak`, claim keeps original amount and `normalisation_status = 'NO_FX'`). `fx_rate`, `fx_rate_date`, `fx_source` are stored on the price observation (I7) |
| Currency inference | `ຕື້`/`ລ້ານ` without a currency word ⇒ `LAK` with confidence −0.10; `$`/`USD`/`ໂດລາ` ⇒ USD; `ບາດ`/`฿`/`บาท` ⇒ THB; otherwise `UNKNOWN` and no `amount_lak` |
| Price basis | `/ຕາແມັດ`, `/m²`, `ຕໍ່ຕາແມັດ` ⇒ `PER_SQM` (total derived only if area known); `/ເດືອນ`, `ຕໍ່ເດືອນ`, `/month` ⇒ `PER_MONTH`; `/ປີ` ⇒ `PER_YEAR` |
| Price type | from class + basis: `PROPERTY_SALE` → `ASKING_SALE`; `PROPERTY_RENT` + month → `ASKING_RENT_MONTHLY`; + year → `ASKING_RENT_YEARLY`; `PER_SQM` on sale → `ASKING_SALE_PER_SQM`; wanted → `WANTED_BUDGET`; else `UNCLASSIFIED_PRICE` (kept, never published) |
| Area | to m², two decimals; `n x m` ⇒ `n·m`, both also recorded as FRONTAGE/DEPTH in metres when 3 ≤ n,m ≤ 500 |
| Text | NFC; Lao digits → ASCII digits for numeric parsing only (evidence span refers to the original) |

## 7. Method tiers

| Tier | Method id | When | Recorded as |
|---|---|---|---|
| 1 | `RULE_V1` | always | `extraction_method = 'RULE_V1'`, `method_version = '<semver of rules module>'` |
| 2 | `LLM_<provider>:<model>` (e.g. `LLM_OLLAMA:qwen2.5:7b`) | optional; only for observations where tier-1 class is `UNCERTAIN` or a lead group fired without a claim; **never** overrides a tier-1 claim — adds claims with its own method id | prompt hash + model + temperature stored on the run; output validated against §3 schema, rejected on any field outside the vocabulary |
| 3 | `HUMAN` | Portal review (001G) | `review_status ∈ {UNREVIEWED, LOW_CONFIDENCE, CONFIRMED, CORRECTED, REJECTED}`; a correction is a **new claim** with `method = 'HUMAN'` linked by `supersedes_claim_id`; the original stays |

Tier 2 is off by default (`LENS_EXTRACT_LLM=`), runs locally only (Ollama on bizera-wsl
per the operator's environment), and its outputs count as claims with their own confidence —
they do not raise tier-1 confidence.

## 8. Run model and API (lens-api 0.6.0, Phase 6)

| Surface | Semantics |
|---|---|
| Background loop | every `LENS_EXTRACT_INTERVAL_MIN` (15) extract records with no current observation, or whose `content_hash` differs from their latest observation's |
| `POST /admin/extract?since=&limit=&force=` | bearer; run now; `force=1` re-runs current `method_version` on already-observed records (new observations, old ones kept) |
| `GET /observations/{record_key}` | bearer; current observation + all claims (+ history when `?all=1`); contact raw value omitted (masked only) |
| `GET /observations?class=&asset=&since=&min_conf=` | bearer; paged listing for Portal/agents |
| `GET /extract/stats` | counts per class/asset, share `UNCERTAIN`, claims without confidence (must be 0), run history |
| `/mcp` | new read-only tools `list_observations`, `get_observation`, `extraction_stats`; no write tools |
| Never | `/records` (L1) is unchanged; no L2 column is added to `records` (ADR-0005 §2.3, CI schema-lint) |

Extraction is idempotent per (`record_key`, `content_hash`, `method_version`): re-running
with the same triple creates no new observation unless `force=1`.

## 9. Invariants honoured / enforced

I1 (nothing deleted; runs append), I2 (every claim has method, confidence, span, review
status — enforced by NOT NULL + CHECK in 001F), I3 (observation ≠ listing ≠ market property —
this unit creates observations only), I4 (price types are `ASKING_*`/`WANTED_BUDGET`; no
transaction type exists), I5 (`OWNER_CLAIMED`, `LAND_TITLE_MENTION` naming), I6 (location
claims carry precision — assigned in 001D, mandatory before any location query), I7 (price
observations append; FX date stored), I9 (all endpoints here are Social Lens-internal),
I10 (contact points hashed + masked; raw restricted; no author aggregation beyond
`ADVERTISER_ROLE` signals on the same record).

## 10. Acceptance gate (freeze criteria; machine-enforced in CI)

1. **Golden fixtures** — `services/lens-api/tests/fixtures/extract/golden-v1.jsonl`: ≥ 100
   hand-labelled real Lao records (text only, no author/contact raw values — contact fields
   are pre-masked in the fixture; labelled by OP-Vily or delegated reviewer). CI asserts:
   classification precision ≥ 0.85 and recall ≥ 0.75 on `PROPERTY_SALE|RENT|WANTED`;
   price and area normalisation exact on ≥ 90 % of records where the field is labelled;
   100 % of claims carry confidence and an evidence span that slices back to `value_text`.
2. **Determinism** — running `RULE_V1` twice on the fixture yields byte-identical output.
3. **No-fact lint** — schema-lint fails any L2 column named `price`, `owner`, `property`,
   `area` without the `_claim`/`_observation`/`_sqm`/`_text` qualifier, or any migration that
   touches `records`, `raw_captures`, `capture_events`, `seen_links`.
4. **Keyword parity** — `src/lib/keywords.ts` groups and the server's groups share
   `keyword_groups_version`; a test compares the two exports.
5. **Live** (STATUS.md evidence): on ≥ 300 real observations, `GET /extract/stats` shows
   `claims_without_confidence = 0` and `UNCERTAIN` share reported (target < 30 %).

Phase 6 exit (ADR-0005) additionally requires 001D's 100 % precision assignment on
location claims.

## 11. Decisions requested (defaults apply if not overruled, per delegation)

| ID | Question | Default |
|---|---|---|
| C1 | Extraction runs server-side inside lens-api (`app/extract/`, pure Python, no ML deps) rather than a separate service or the extension? | **Yes** — L2 writers are "Social Lens workers" (001A §4); keeps one deploy unit on bizera-wsl |
| C2 | FX source for LAK normalisation = Bank of the Lao PDR reference rate, loaded manually/CSV first, automated later? | **Yes**; `fx_source` recorded either way |
| C3 | Tier-2 LLM assistance = local Ollama only, off by default, never overriding tier 1? | **Yes** |
| C4 | Contact raw value column encrypted at rest (pgcrypto, key from `.env`) in 001F? | **Yes** |
| C5 | Golden fixture labelling: 100 records from the first 7-day 0.7.0 capture, labelled in a Portal-less CSV round-trip (export → label → import)? | **Yes**; Portal review UI is 001G |

## 12. Out of scope (next units)

001D geo resolution and admin geography copy (D4), 001E clustering/entity resolution,
001F DDL for `extract.*`/`geo.*`, 001G review workflow and reviewer role, 001H publication.
