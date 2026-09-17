> Frozen parent artefact (OP-Vily, 2026-09-17). Source: architecture review notes; kept verbatim. Governing child: SLL-PROP-DATA-001A.

Agreed. I would freeze this as the **parent architecture artifact** before moving into SQL/schema implementation.

## `SLL-PROP-DATA-001`

### Social Lens Property Market Intelligence Data Architecture v0.1

**Purpose:** Define the authoritative data architecture by which BST Social Lens converts captured social-media evidence into normalized, provenance-preserving real-estate market intelligence that can later be consumed by BizProp+ without coupling the two systems.

The architecture boundary is:

```text
BST Social Lens Extension
        │
        │ Capture
        ▼
┌──────────────────────┐
│  Social Lens Portal  │
│                      │
│ Raw Evidence         │
│ Source Records       │
│ Property Extraction  │
│ Geo Normalization    │
│ Entity Resolution    │
│ Market Intelligence  │
│ Quality / Review     │
│ Published Dataset    │
└──────────┬───────────┘
           │
           │ Versioned Reference API
           ▼
┌──────────────────────┐
│      BizProp+        │
│                      │
│ Asset / Parcel       │
│ Valuation            │
│ Comparables          │
│ Market Analytics     │
└──────────────────────┘
```

### Five frozen contracts

| Contract                              | Responsibility                          | Key invariant                                                      |
| ------------------------------------- | --------------------------------------- | ------------------------------------------------------------------ |
| **C01 Capture Contract**              | Extension → Social Lens                 | Original evidence remains traceable and immutable                  |
| **C02 Property Extraction Contract**  | Evidence → structured claims            | AI/extraction output is a **claim**, not automatically a fact      |
| **C03 Geo-Normalization Contract**    | Free-text/maps → canonical geography    | Location precision and confidence must be explicit                 |
| **C04 Entity-Resolution Contract**    | Observations → market-property clusters | Same-property resolution never destroys source observations        |
| **C05 BizProp+ Publication Contract** | Social Lens → BizProp+                  | Only controlled/published market intelligence crosses the boundary |

### Canonical data lineage

This should become a non-negotiable invariant:

```text
RAW CAPTURE
    ↓
SOURCE RECORD
    ↓
PROPERTY OBSERVATION
    ↓
EXTRACTED CLAIMS
    ↓
NORMALIZED CLAIMS
    ↓
ENTITY RESOLUTION
    ↓
MARKET PROPERTY
    ↓
QUALITY / REVIEW
    ↓
PUBLISHED MARKET DATA
    ↓
BIZPROP+ MARKET REFERENCE
```

Critically:

```text
Source Post ≠ Listing ≠ Market Property ≠ BizProp Asset
```

And:

```text
Asking Price ≠ Transaction Price
Claimed Owner ≠ Verified Owner
Social Coordinate ≠ Cadastral Parcel
Market Property ≠ Land Title
```

### Same-property/multiple-agent invariant

For your primary business case:

```text
Agent A Post ── ₭2.50B ──┐
Agent B Post ── ₭2.70B ──┤
Company C ───── ₭2.45B ──┼──► MP-000127
Owner-claimed ─ ₭2.30B ──┘
```

The database retains **four observations and four price claims**, while the entity-resolution layer may associate them with one `market_property_id`.

Therefore MP-000127 can derive:

```text
observation_count       = 4
unique_advertisers      = 4

min_asking_price        = ₭2.30B
max_asking_price        = ₭2.70B
latest_asking_price     = ...
median_asking_price     = ...

first_observed_at       = ...
last_observed_at        = ...

resolution_confidence   = ...
```

No original evidence disappears.

## Controlled decomposition

I would **not jump directly from `SLL-PROP-DATA-001` into coding**. Decompose the parent into controlled child work packages:

```text
SLL-PROP-DATA-001
│
├── 001A — Domain & Data Boundary
│     Define terminology, ownership,
│     SoR boundaries and invariants
│
├── 001B — Capture & Provenance Contract
│     Raw/source/evidence lifecycle
│
├── 001C — Property Extraction Contract
│     Classification, field extraction,
│     evidence spans and confidence
│
├── 001D — Geo-Normalization Contract
│     Lao admin hierarchy, coordinates,
│     map links and spatial precision
│
├── 001E — Entity-Resolution Contract
│     source dedup, listing clustering,
│     same-property resolution
│
├── 001F — Canonical Database Design
│     PostgreSQL/PostGIS schemas,
│     ERD, keys, constraints, indexes
│
├── 001G — Data Quality & Review
│     validation, confidence,
│     exceptions, human review
│
└── 001H — BizProp+ Publication Contract
      API schemas, dataset versions,
      comparable-data boundary
```

The first executable controlled unit should therefore be **`SLL-PROP-DATA-001A — Domain & Data Boundary v0.1`**, not the database DDL.

Its acceptance criterion is simple but important: before designing tables, we must be able to answer deterministically **what each entity means, which system owns it, what constitutes evidence versus a claim versus a resolved entity, what may be merged, what must never be deleted through deduplication, and exactly what BizProp+ is permitted to consume**.

Once `001A` is frozen, `001B–001E` define the contracts. Only then should `001F` turn those semantics into the production PostgreSQL/PostGIS ERD and schema. This avoids encoding an ambiguous concept such as “duplicate property” into the database and having to unwind it later.
