> Foundation notes behind SLL-PROP-DATA-001 (OP-Vily, 2026-09-17). Kept verbatim as design input; the parent + 001A are the governing text.

Yes. The key architectural correction is to treat **Social Lens as an independent market-observation/data-refinement platform**, not as a BizProp+ scraping subsystem.

I reviewed the current [BST Social Lens repository](https://github.com/bstBizEra/BST-Social-Lens?utm_source=chatgpt.com) and its accepted architecture. The extension already captures Facebook/TikTok content from the operator's logged-in browser session, buffers it in Dexie/IndexedDB, and pushes records through an ingest API. Its current v1 record includes post identity, permalink, container, author, text, timestamps, engagement, media, hashtags, parser version and sync state.  The accepted architecture also already defines PostgreSQL LensDB as the server system of record and FastAPI as the only database boundary.

The new requirement should extend this foundation rather than redesign the collector.

# 1. Recommended system boundary

I would establish three independent products:

```text
┌──────────────────────────────────────────────┐
│ BST SOCIAL LENS — FIELD COLLECTION          │
│ Browser Extension / Field Agent             │
│                                              │
│ Discover → Capture → Preserve Evidence       │
└──────────────────────┬───────────────────────┘
                       │
                       │ observations
                       ▼
┌──────────────────────────────────────────────┐
│ SOCIAL LENS PORTAL                          │
│ Market Intelligence Data Platform           │
│                                              │
│ Ingest → Extract → Clean → Normalize         │
│ → Geocode → Deduplicate → Cluster            │
│ → Review → Organize → Publish Dataset        │
└──────────────────────┬───────────────────────┘
                       │
                 Reference API
                       │
                       ▼
┌──────────────────────────────────────────────┐
│ BIZPROP+                                     │
│ Property / Valuation Platform                │
│                                              │
│ Internal Asset Data                          │
│ + Land Title Data                            │
│ + Inspection Data                            │
│ + Social Lens Market Observations            │
│ + Comparable Evidence                        │
│                  ↓                           │
│          Valuation / Market Analytics        │
└──────────────────────────────────────────────┘
```

**Social Lens owns observed market evidence. BizProp+ owns property/valuation decisions.**

BizProp+ should therefore never query Social Lens tables directly. It consumes a versioned API or published dataset.

---

# 2. The most important data-model distinction

A social-media post is **not a property**.

And multiple social posts about the same land are **not duplicate records that should simply be deleted**.

The correct hierarchy is:

```text
Source Post
    ↓
Observation
    ↓
Extracted Listing Claim
    ↓
Candidate Property / Market Listing
    ↓
Entity Resolution
    ↓
Canonical Market Property
```

For example:

```text
Agent A Facebook post ─────┐
                           │
Agent B Facebook post ─────┤
                           ├──► SAME MARKET PROPERTY
Company C Facebook post ───┤
                           │
Owner TikTok post ─────────┘
```

All four observations must survive.

That history tells BizProp+:

* how many agents advertise the property;
* whether prices differ;
* whether price changes over time;
* when it first appeared;
* when it was last observed;
* whether multiple sellers give conflicting areas/locations;
* how much independent evidence exists.

So I recommend **entity resolution**, not destructive deduplication.

---

# 3. Revised end-to-end flow

The target pipeline should be:

```text
DISCOVER
   ↓
CAPTURE
   ↓
RAW PRESERVATION
   ↓
CLASSIFY
   ↓
EXTRACT
   ↓
NORMALIZE
   ↓
GEO-RESOLVE
   ↓
ENTITY RESOLUTION
   ↓
CLUSTER
   ↓
HUMAN REVIEW
   ↓
CANONICAL MARKET RECORD
   ↓
PRICE OBSERVATION
   ↓
QUALITY / CONFIDENCE
   ↓
PUBLISH
   ↓
BIZPROP+ REFERENCE API
```

I would formally call this:

**Capture → Evidence → Observation → Claim → Entity → Market Intelligence**

That terminology will prevent substantial database problems later.

---

# 4. Stage A — Discover

Your initial Lao keyword set is already strongly aligned with ADR-0002. The repository specifically defines Lao-aware matching using NFC normalization and substring matching rather than `\b`, because Lao word boundaries cannot be treated like English. It also distinguishes fields such as post date/container type from keywords.

I would expand the property taxonomy beyond one flat keyword list:

| Keyword group | Examples                                       |
| ------------- | ---------------------------------------------- |
| Asset         | `ດິນ`, `ທີ່ດິນ`, `ເຮືອນ`, `ອາຄານ`              |
| Intent        | `ຂາຍ`, `ເຊົ່າ`, `ຊື້`, `ຕ້ອງການ`               |
| Price         | `ລາຄາ`, `ກີບ`, `ບາດ`, `USD`, `$`               |
| Area          | `ເນື້ອທີ່`, `m2`, `m²`, `ເຮັກຕາ`               |
| Location      | `ບ້ານ`, `ເມືອງ`, `ແຂວງ`                        |
| Map           | `location`, `Google Map`, `map`, `lat`, `long` |
| Sales         | `ເຈົ້າຂອງ`, `ນາຍໜ້າ`, `agent`                  |

Then use rule combinations:

```text
PROPERTY_LEAD =
    asset keyword
    +
    transaction-intent keyword

HIGH_VALUE_LEAD =
    PROPERTY_LEAD
    +
    (price OR area OR location)
```

This will reduce noise substantially.

---

# 5. Stage B — Raw capture must remain immutable

Do not immediately transform the captured post into a property table.

Preserve the evidence first:

```text
raw_capture
├── platform
├── source_post_id
├── permalink
├── captured_at
├── post_created_at
├── raw_text
├── author_hash
├── container
├── media
├── engagement
├── parser_version
├── raw_payload_hash
└── raw_payload / evidence reference
```

This extends the existing Social Lens capture contract rather than replacing it.

Think of this as **Bronze data**.

---

# 6. Stage C — Property classification

Every observation should first answer:

```text
Is this actually a real-estate market signal?
```

Recommended:

```text
PROPERTY_SALE
PROPERTY_RENT
PROPERTY_WANTED
AGENT_ADVERTISEMENT
DEVELOPER_PROJECT
PRICE_DISCUSSION
MARKET_INFORMATION
NON_PROPERTY
UNCERTAIN
```

Then classify asset type:

```text
LAND
HOUSE
APARTMENT
COMMERCIAL
WAREHOUSE
HOTEL
FARM
DEVELOPMENT_LAND
BUILDING
OTHER
UNKNOWN
```

Do not require every record to classify successfully.

`UNKNOWN` is much safer than hallucinated structured data.

---

# 7. Stage D — Extraction: preserve claim vs normalized value

This is particularly important for Lao social content.

Suppose the post says:

> ຂາຍດິນ 20x30 ບ້ານນາສ້າງໄຜ່ ລາຄາ 2.5 ຕື້

Store both:

```text
source_text:
"ຂາຍດິນ 20x30 ... ລາຄາ 2.5 ຕື້"

area_text:
"20x30"

area_normalized_sqm:
600

price_text:
"2.5 ຕື້"

price_normalized:
2500000000

currency:
LAK
```

Each extracted field should also carry:

```text
extraction_method
confidence
evidence_span
review_status
```

So AI extraction never silently becomes fact.

---

# 8. Stage E — Location resolution

Location needs its own resolution pipeline:

```text
Post Text
   │
   ├── Village text
   ├── District text
   ├── Province text
   ├── Google Maps URL
   ├── Lat/Long
   └── Location description
          ↓
   LOCATION RESOLVER
          ↓
 Lao Administrative Master
          ↓
 canonical location
```

Recommended normalized result:

```text
province_id
district_id
village_id

latitude
longitude
geom

location_precision
location_source
location_confidence
```

Precision:

```text
EXACT_COORDINATE
PARCEL_APPROXIMATE
VILLAGE
DISTRICT
PROVINCE
TEXT_ONLY
UNKNOWN
```

This matters enormously for price analytics. A village-level observation must not masquerade as an exact parcel coordinate.

PostGIS is a strong fit here. `ST_DWithin` can efficiently identify observations within a radius and uses available spatial indexes; `ST_Within` can resolve a point against administrative polygons. ([PostGIS][1])

---

# 9. Stage F — The dedup problem actually has three layers

The current Social Lens architecture already deduplicates captured records by `${platform}:${post_id}` and ADR-0002 separately maintains a normalized URL frontier.

Keep that, but add two more levels.

### Level 1 — Source duplicate

```text
same Facebook post
same TikTok post
same permalink
```

→ merge capture events into one `source_record`.

### Level 2 — Reposted advertisement

```text
same text
same images
same phone
same price
same location
different post
```

→ retain posts separately but link them into one `listing_cluster`.

### Level 3 — Same physical property

```text
different text
different agent
different images
different price
same underlying land/property
```

→ link observations to one `market_property`.

This third level is where most of the business value resides.

---

# 10. Property entity-resolution engine

I recommend scoring candidate matches instead of a binary duplicate rule.

Conceptually:

```text
Entity Match Score =
    Geo proximity
  + Area similarity
  + Price similarity
  + Phone/contact overlap
  + Image similarity
  + Text similarity
  + Village/District agreement
  + Landmark agreement
  + Google Maps agreement
  + Temporal relationship
```

Example:

| Signal                    | Weight |
| ------------------------- | -----: |
| Exact coordinate          |     30 |
| Within 30 m               |     25 |
| Same Google Maps location |     25 |
| Area ±2%                  |     15 |
| Same phone                |     15 |
| Image perceptual match    |     15 |
| Same village              |      8 |
| Similar description       |      8 |
| Price ±5%                 |      5 |

Then:

```text
>= 80  → HIGH_CONFIDENCE_MATCH
60–79  → REVIEW_REQUIRED
< 60   → SEPARATE_CANDIDATE
```

The numbers should be calibrated against actual Lao property data; they are proposed starting points, not frozen business rules.

---

# 11. Do not collapse agent listings into one price

Suppose:

```text
Agent A → ₭2.5B
Agent B → ₭2.7B
Agent C → ₭2.45B
Owner   → ₭2.3B
```

The canonical property should **not** become:

```text
price = 2.5B
```

Instead:

```text
MARKET PROPERTY #MP-001

├── Observation A → ₭2.50B
├── Observation B → ₭2.70B
├── Observation C → ₭2.45B
└── Observation D → ₭2.30B
```

Then derive:

```text
latest asking price
minimum observed price
maximum observed price
median asking price
price dispersion
first observed
last observed
listing count
unique advertiser count
price-change history
```

That becomes valuable comparable-market intelligence.

---

# 12. Recommended canonical database

I would evolve `LensDB` into schemas rather than one giant posts table:

```text
lensdb

raw/
  captures
  payloads
  media

source/
  platforms
  posts
  comments
  authors
  containers
  seen_links

extract/
  extraction_runs
  property_claims
  field_evidence

geo/
  provinces
  districts
  villages
  places
  geocode_results

market/
  market_properties
  property_observations
  listing_clusters
  advertisers
  contacts
  prices
  areas
  locations
  media_signatures

resolution/
  entity_candidates
  entity_matches
  entity_decisions

quality/
  validation_results
  review_queue
  confidence_scores

publish/
  datasets
  dataset_versions
  bizprop_exports

audit/
  events
```

PostgreSQL + PostGIS remains the natural core.

---

# 13. Core entity relationship

```text
source_post
    │
    │ 1:N
    ▼
property_observation
    │
    ├──── price_observations
    ├──── area_claims
    ├──── location_claims
    ├──── contact_claims
    └──── evidence
    │
    │ N:1
    ▼
market_property
    │
    ├──── listing_clusters
    ├──── property_locations
    ├──── price_history
    └──── quality_profile
```

This is the heart of the design.

---

# 14. `source_posts`

```text
source_post_id          UUID PK
platform                facebook/tiktok/...
platform_post_id
permalink
container_type
container_name

author_hash
author_display_name

raw_text
language

post_created_at
first_captured_at
last_captured_at

reactions
comments
shares
views

parser_version
content_hash

UNIQUE(platform, platform_post_id)
```

The existing repository already establishes this source-level identity model.

---

# 15. `property_observations`

This should be the principal clean-data fact table.

```text
observation_id              UUID PK
source_post_id              FK

transaction_type
property_type

asking_price
currency_code
price_basis

land_area_sqm
frontage_m
depth_m

province_id
district_id
village_id

latitude
longitude
geom

location_precision

google_maps_url

post_date
first_seen_at
last_seen_at

advertiser_id

extraction_confidence
location_confidence
property_confidence

review_status
quality_status

market_property_id          NULLABLE FK
```

---

# 16. `market_properties`

This is **not a legal land-title master**.

It represents Social Lens's best belief that multiple observations refer to the same real-world market asset.

```text
market_property_id
canonical_property_type

canonical_location_geom

province_id
district_id
village_id

estimated_land_area_sqm

first_observed_at
last_observed_at

observation_count
advertiser_count

resolution_confidence

status
```

Use terminology such as:

> `market_property`

rather than:

> `property`

because Social Lens has observational evidence, not authoritative land-registry evidence.

---

# 17. Advertiser model

Do not assume:

```text
Facebook author = property owner
```

Use:

```text
advertiser
├── observed_identity
├── company
├── freelance_agent
├── owner_claimed
├── unknown
└── contact_points
```

Roles:

```text
OWNER_CLAIMED
FREELANCE_AGENT
COMPANY_AGENT
DEVELOPER
COMPANY
UNKNOWN
```

Critically, `OWNER_CLAIMED` means:

> the source claimed to be the owner.

It does not mean Social Lens has legally verified ownership.

---

# 18. Contact handling

Phone numbers are powerful entity-resolution signals but require tighter access controls than general listing data.

I would separate:

```text
advertiser_contacts
```

from the public/reference market dataset.

Store:

```text
contact_type
normalized_value
value_hash
masked_value
first_seen
last_seen
```

The matching engine can use a normalized/hash representation while BizProp+ need not receive raw personal contact data.

This is consistent with the existing Social Lens privacy posture: author IDs are hashed by default and the extension is explicitly designed around post/comment intelligence rather than person profiling.

---

# 19. Price observation model

I strongly recommend a separate price fact table:

```text
price_observation_id
observation_id
market_property_id

price_type
amount_original
currency_original

amount_lak
fx_rate
fx_rate_date

price_per_sqm

observed_at
source_post_date

confidence
```

Price types:

```text
ASKING_SALE
ASKING_RENT_MONTHLY
ASKING_RENT_YEARLY
NEGOTIABLE
PRICE_REDUCED
UNKNOWN
```

Do not mix asking price with actual transaction price.

That distinction becomes essential when BizProp+ starts producing valuation comparables.

---

# 20. Temporal model

Social-market intelligence should be time-series first.

A property advertised:

```text
Jan → ₭3.0B
Mar → ₭2.8B
Jun → ₭2.5B
```

contains much more information than the final `₭2.5B`.

Therefore preserve:

```text
valid_from
valid_to
observed_at
first_seen_at
last_seen_at
post_created_at
captured_at
```

Do not overwrite previous asking prices.

---

# 21. Social Lens Portal workflow

The Portal should operate as a data-workbench rather than merely a dashboard.

```text
INBOX
 ↓
NEW CAPTURES
 ↓
PROPERTY CANDIDATES
 ↓
EXTRACTION REVIEW
 ↓
LOCATION REVIEW
 ↓
DUPLICATE / ENTITY MATCH REVIEW
 ↓
CANONICAL PROPERTY
 ↓
QUALITY CHECK
 ↓
PUBLISHED MARKET DATA
```

Primary Portal screens should therefore be:

**Capture Inbox → Property Leads → Extraction Review → Map Review → Duplicate/Match Review → Market Property 360 → Advertiser View → Price History → Data Quality → Published Dataset → API Management.**

---

# 22. Property 360 screen

A canonical market-property page should show:

```text
MARKET PROPERTY MP-0000127
────────────────────────────

Location
📍 Nasangphai, Xaysetha, Vientiane

Observed Area
600 m²

Observed Price Range
₭2.30B — ₭2.70B

Price / m²
₭3.83M — ₭4.50M

First Seen
12 Jan 2026

Last Seen
14 Sep 2026

Advertisements
17

Unique Advertisers
6

Confidence
High

────────────────────────────
OBSERVATIONS

Facebook Agent A    ₭2.50B
Facebook Agent B    ₭2.70B
TikTok Agent C      ₭2.45B
Facebook Owner?     ₭2.30B
...
```

This is substantially more useful than presenting 17 apparently independent listings.

---

# 23. Data-quality scoring

I recommend a field-coverage score:

```text
DQ =
  Price        20%
+ Area         15%
+ Location     20%
+ Coordinate   15%
+ Post Date    10%
+ Evidence     10%
+ Entity Match 10%
```

And publish:

```text
A = 90–100
B = 75–89
C = 60–74
D = <60
```

But keep **data quality** separate from **entity-resolution confidence**.

A complete listing can still be linked to the wrong property.

---

# 24. BizProp+ integration contract

The boundary should be one-way initially:

```text
Social Lens Portal
       │
       │ Published Reference API
       ▼
BizProp+
```

Not:

```text
BizProp+ ──SQL──► LensDB
```

Recommended API families:

```text
GET /v1/market/properties
GET /v1/market/properties/{id}

GET /v1/market/observations
GET /v1/market/price-observations

GET /v1/market/comparables
GET /v1/market/statistics

GET /v1/market/nearby
```

For example:

```text
/market/nearby
?lat=17.97
&lon=102.63
&radius_m=2000
&property_type=LAND
&from=2026-01-01
```

PostGIS `ST_DWithin` is particularly suitable for this radius filtering because it is spatial-index aware. ([PostGIS][2])

---

# 25. How BizProp+ should consume it

For an evaluated parcel:

```text
BizProp+ Subject Property
│
├── Land Title
├── Inspection
├── Parcel Geometry
├── User-entered comparable
│
└── Query Social Lens
       ↓
    Radius 500m / 1km / 3km
       ↓
    Same / similar asset type
       ↓
    Relevant observation period
       ↓
    Similar area
       ↓
    Published observations
       ↓
    Candidate Comparable Set
```

But Social Lens records should initially be classified as:

```text
OBSERVED ASKING PRICE
```

rather than:

```text
VERIFIED TRANSACTION PRICE
```

That distinction should be visible in the valuation engine.

---

# 26. Recommended comparable hierarchy

Eventually BizProp+ can distinguish:

```text
LEVEL A
Verified registered transaction

LEVEL B
Verified institutional comparable

LEVEL C
Verified broker/agent transaction evidence

LEVEL D
Social Lens asking-price observation

LEVEL E
Unverified market indication
```

Social Lens will massively expand market coverage, but it should not falsely elevate scraped asking prices to transaction evidence.

---

# 27. Architecture after this extension

The existing three-tier Social Lens architecture remains valid.  I would evolve it into:

```text
BST Social Lens Extension
        │
        ▼
Lens Ingest API
        │
        ▼
RAW / SOURCE
        │
        ▼
Extraction Worker
        │
        ▼
Normalization Worker
        │
        ▼
Geo Resolver
        │
        ▼
Entity Resolution Engine
        │
        ├──────────────┐
        ▼              ▼
Review Queue      Auto Accepted
        │              │
        └──────┬───────┘
               ▼
        Market Intelligence DB
               │
       ┌───────┴────────┐
       ▼                ▼
Social Lens Portal   Publish API
                        │
                        ▼
                     BizProp+
```

I would **not introduce OpenSearch initially**. PostgreSQL 16 + PostGIS + `pg_trgm` is sufficient for the first controlled implementation. OpenSearch/vector retrieval becomes justified later if semantic search, very large corpora, multimodal similarity or high-scale discovery materially exceeds PostgreSQL's role. The existing ADR already defers Lao-specific full-text search and uses `pg_trgm` for now.

---

# 28. Recommended controlled data layers

I would formalize four layers:

```text
L0 — RAW
Immutable captured evidence

L1 — OBSERVATION
Normalized source posts/comments

L2 — INTELLIGENCE
Extracted property claims + entity resolution

L3 — PUBLISHED
Quality-controlled market reference dataset
```

Only **L3** should be available to BizProp+ by default.

That gives a clean provenance chain:

```text
BizProp Comparable
      ↓
Published Market Property
      ↓
Property Observation
      ↓
Extracted Claim
      ↓
Source Post
      ↓
Raw Capture
```

Every number in a valuation can therefore be traced back to its market evidence.

---

# 29. One additional concept I strongly recommend: `Market Property ≠ BizProp Asset`

Never merge these IDs.

```text
Social Lens
market_property_id = MP-xxxx

BizProp+
asset_id = AST-xxxx
```

BizProp+ can create:

```text
asset_market_reference
├── asset_id
├── market_property_id
├── relationship
├── match_score
├── match_method
├── verified_by
└── verified_at
```

Possible relationships:

```text
SAME_PROPERTY
POSSIBLE_SAME_PROPERTY
COMPARABLE
NEARBY
REJECTED_MATCH
```

That preserves system autonomy and prevents uncertain social evidence from contaminating BizProp+'s canonical asset master.

---

# 30. Recommended next controlled design

I recommend naming the next artifact:

**`SLL-PROP-DATA-001 — Social Lens Property Market Intelligence Data Architecture v0.1`**

Its frozen scope should contain five contracts: **Capture Contract → Property Extraction Contract → Geo-Normalization Contract → Entity-Resolution Contract → BizProp+ Publication Contract**.

The central design principle should be:

> **Never deduplicate away market evidence. Deduplicate source records, cluster advertisements, resolve real-world property entities, and preserve every price/location claim with provenance.**

That design directly solves your case where the **same land is advertised by multiple freelance agents, companies, and potentially the owner at different prices**, while turning Social Lens from a scraper database into a defensible **Lao real-estate market intelligence layer** for BizProp+.

[1]: https://postgis.net/docs/ST_DWithin.html?utm_source=chatgpt.com "ST_DWithin"
[2]: https://postgis.net/documentation/tips/st-dwithin/?utm_source=chatgpt.com "Use ST_DWithin for radius queries | PostGIS"
