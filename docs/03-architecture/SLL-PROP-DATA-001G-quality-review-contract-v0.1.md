# SLL-PROP-DATA-001G — Quality & Review Contract v0.1

- Parent: `SLL-PROP-DATA-001` (frozen) · Boundary: `001A` (frozen) · Inputs: `001C` claims, `001D` resolutions, `001E` decisions · Physical: `001F` · Roadmap: ADR-0005 Phase 8 → v1.0.0
- Status: **Draft for freeze** (2026-09-17). Freezes when the review workflow has run on real queues for two weeks and §9 holds. Implementation state: §2 DQ scorer + validation rules (`app/quality/dq.py`) and §5 `audit.events` (`schema_audit.sql`, applied) done; §3 extraction/location queues and §6 CSV protocol done (`scripts/review.py`); `GET /quality/stats` done; §4 roles, match queue and the remaining §7 endpoints pending 001F.
- Contract family: supports **C05 Publication** (only reviewed, graded intelligence is publishable) and closes the review states opened by 001C §7, 001D §3, 001E §7.

## 1. Purpose and scope

Make L2 defensible before anything crosses to L3: a **Data-Quality (DQ) grade** on every
observation and market property that is *separate from* resolution confidence (a complete
listing can still be linked to the wrong property), a single **review model** for the three
queues the earlier contracts created (claims, locations, entity decisions), a **reviewer
role** with controlled access to the restricted columns, a full **audit trail**, and the
**CSV round-trip protocol** that stands in for the Portal until Phase 7's workbench ships.

Out of scope: publication itself (001H), the Portal UI (Phase 7 deliverable — this contract
is what it must implement), legal/compliance content (parked register; a precondition for
publishing, not for review).

## 2. DQ score and grade

Field-coverage score per **observation** (foundation notes §23), weights fixed here:

| Component | Weight | Full credit when | Partial |
|---|---|---|---|
| Price | 20 | a PRICE claim with `amount_lak` (FX resolved) and confidence ≥ 0.6 | 10 if amount present but `NO_FX`; 0 otherwise |
| Area | 15 | AREA claim with `area_sqm`, confidence ≥ 0.6 | 8 if confidence 0.3–0.6 |
| Location | 20 | primary resolution precision ≥ `VILLAGE` | 12 at `DISTRICT`, 6 at `PROVINCE`, 0 at `TEXT_ONLY`/`UNKNOWN` |
| Coordinate | 15 | precision `EXACT_COORDINATE` | 10 at `PARCEL_APPROXIMATE`; 0 otherwise |
| Post date | 10 | `post_date` present | 0 |
| Evidence | 10 | `first_payload_hash` resolves to a stored raw capture (001B) | 5 if hash present but body purged; 0 if no hash |
| Entity match | 10 | current decision is `CONFIRMED` or `HIGH_CONFIDENCE_MATCH` | 5 if `REVIEW_REQUIRED`/singleton; 0 if `REJECTED`/`UNLINKED` |

Grade: **A 90–100 · B 75–89 · C 60–74 · D < 60**. Market-property DQ = observation-count-weighted
mean of its current observations' scores, graded on the same scale, stored on
`market.property_stats_snapshots.dq_grade` (001F) with `dq_version`. DQ never feeds the matcher
(001E) and resolution confidence never feeds DQ.

Validation rules (produce **exceptions**, never silent fixes): price outside 1 M–100 B LAK;
area < 4 m² or > 500 ha; per-m² outside 10 k–500 M LAK; post date in the future or before 2015;
price observation without claim; observation with location claims but no resolution row;
market property with zero current observations; contact point with > 200 sightings.

## 3. One review model, three queues

| Queue | Item | Opened by | Machine state on entry |
|---|---|---|---|
| **Extraction** | `extract.claims` | 001C: `review_status = LOW_CONFIDENCE` (< 0.3); **or** confidence < 0.5 (mid-confidence claims such as a bare number near a price term); or any claim on an observation with `UNCERTAIN` class and lead groups present | `UNREVIEWED` / `LOW_CONFIDENCE` |
| **Location** | `geo.resolved_locations` | 001D: `LOW_CONFIDENCE`, `conflict_text_vs_point`, `*_ambiguous` | `UNREVIEWED` / `LOW_CONFIDENCE` |
| **Match** | `resolution.entity_decisions` | 001E: `REVIEW_REQUIRED`; DQ exceptions of kind "entity" | `REVIEW_REQUIRED` |
| **Exceptions** | validation rule hits (§2) | 001G | `OPEN` |

Shared state machine (every item, every queue):

```
UNREVIEWED / LOW_CONFIDENCE / REVIEW_REQUIRED / OPEN
        │ reviewer acts
        ├─► CONFIRMED   (machine value stands; reviewer attests)
        ├─► CORRECTED   (new row with method/source = HUMAN, supersedes the reviewed row)
        ├─► REJECTED    (row marked wrong; no replacement; downstream treats as absent)
        └─► DEFERRED    (needs more evidence; re-queued after N days or on new capture)
```

Rules: a review never edits a machine row — it **supersedes** it (001F R8); `CORRECTED`
writes the new value with `reviewer` set; `REJECTED` on a decision equals `UNLINKED` in 001E
terms; a `HUMAN` row is never overwritten by a later machine run (E4); every action writes
one `audit.events` row (§5). Queue priority: exceptions → match → location → extraction, then
by observation DQ descending (fixing high-value items first) and age ascending.

## 4. Reviewer role and restricted data

| Role | May read | May write | Never |
|---|---|---|---|
| `viewer` (BST agents via `/mcp`, Console) | L1/L2 minus restricted columns | nothing | contact raw values, author ids |
| `reviewer` | everything `viewer` can + masked contacts, decision history, DQ exceptions | review actions (§3), aliases (001D G3), merge/split (001E) | contact raw values (still) |
| `reviewer_contacts` | `reviewer` + decrypted `contact_points.raw_value_enc` for the item under review, one at a time, logged | same as `reviewer` | bulk export of raw contacts; any publish action |
| `publisher` | as `reviewer` | create/publish/withdraw dataset versions (001H) | review actions on items in the version being published (separation of duties) |

Physical form (001F open item 2): PostgreSQL roles mapped to the API's per-user tokens
(Phase 7 Portal auth via MCP Hub); `raw_value_enc` decrypted only through
`GET /review/contacts/{hash}` with `reviewer_contacts`, which logs the access. No endpoint
returns more than one decrypted value per call. Until the Portal exists, `reviewer_contacts`
is exercised only by OP-Vily through the CSV protocol (§6), which **never** includes raw
contact values.

## 5. Audit trail

`audit.events` (append-only; new schema, lint rules apply): `event_id, at, actor, role,
queue, item_table, item_id, action ∈ {CONFIRM, CORRECT, REJECT, DEFER, MERGE, SPLIT, ALIAS,
CONTACT_REVEAL, PUBLISH, WITHDRAW}, before (jsonb), after (jsonb), reason, batch_id`. Every
review action and every restricted read produces exactly one row; `batch_id` groups a CSV
import. Nothing in `audit.*` is ever deleted or purged.

## 6. CSV round-trip protocol (until the Portal)

Extends `scripts/golden.py` into `scripts/review.py`:

1. `export --queue {extraction|location|match|exceptions} --limit N --out DIR` writes one CSV
   per queue with item id, the machine value(s), the evidence (spans / signals / distances),
   DQ score, and empty `action` / `corrected_value` / `reason` columns. Contact values appear
   **masked only**.
2. OP-Vily fills `action ∈ {CONFIRM, CORRECT, REJECT, DEFER}` (+ `corrected_value` for
   CORRECT, in the same vocabulary as the machine column).
3. `import --file CSV --reviewer <id>` validates every row (vocabulary, item exists, item still
   in the exported state — otherwise the row is skipped with a reason), writes the superseding
   rows and one `audit.events` row per action under one `batch_id`, recomputes DQ for the
   touched observations/properties, and prints a summary.
4. The CSV files are working aids: they are not committed and are deleted after import
   (E6/001A: no record data in the repository).

## 7. Endpoints / MCP (Phase 8)

| Surface | Semantics |
|---|---|
| `GET /review/queue?queue=&limit=&min_dq=` | paged items with evidence (reviewer) |
| `POST /review/actions` | `[{queue, item_id, action, corrected_value?, reason}]` → superseding rows + audit (reviewer) |
| `GET /review/contacts/{hash}` | one decrypted value, logged (`reviewer_contacts`) |
| `GET /quality/stats` | DQ distribution by grade for observations and properties, exception counts by rule, queue sizes and ages (p50/p95), review throughput |
| `POST /admin/quality/recompute?since=` | recompute DQ snapshots (new rows) |
| `/mcp` | read-only `quality_stats`, `review_queue_summary` (counts only, no item content) |

## 8. Invariants honoured

I1 (reviews supersede, never delete), I2 (DQ is a coverage score, not a truth claim; grades
travel with the data), I5/I6 (location components of DQ respect precision), I8 (every action
audited and reversible by a further superseding row), I9 (nothing here crosses to BizProp+ —
001H decides what does, using DQ grade as a filter), I10 (restricted reads are one-at-a-time,
logged; CSVs never carry raw contacts).

## 9. Acceptance gate (freeze criteria)

1. DQ computed for 100 % of current observations and properties; `dq_version` recorded;
   recompute is deterministic (CI test on fixtures).
2. Every review action in a two-week live trial has exactly one audit row; no machine row
   was updated in place (assertion over `updated_at`-free tables, 001F R8).
3. CSV round-trip: export → edit → import on ≥ 100 items across all queues with zero
   silent skips (every skipped row has a reason).
4. Restricted access: no endpoint or CSV exposes a raw contact value; `CONTACT_REVEAL` count
   equals the number of decrypt calls.
5. Queue throughput: p95 age ≤ 1 business day at expected volume (also 001E §11.6).

## 10. Decisions requested (defaults apply if not overruled, per delegation)

| ID | Question | Default |
|---|---|---|
| Q1 | DQ weights fixed as §2 (foundation notes), with partial-credit rules as written; revisited only with a `dq_version` bump? | **Yes** |
| Q2 | One state machine for all queues (CONFIRM/CORRECT/REJECT/DEFER) rather than queue-specific verbs? | **Yes** |
| Q3 | Separation of duties: `publisher` cannot review items in the version being published? | **Yes** |
| Q4 | Contact reveal only one-at-a-time, logged, and only from the Portal (never via CSV)? | **Yes** |
| Q5 | `DEFERRED` items re-queue after 14 days or immediately on a new capture of the same record? | **Yes** |

## 11. Out of scope

Portal screens (Phase 7), publication (001H), legal register (parked, precondition for 001H
execution only).
