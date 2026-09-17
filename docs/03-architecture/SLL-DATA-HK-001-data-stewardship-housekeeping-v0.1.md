# SLL-DATA-HK-001 — Data Stewardship & Housekeeping Architecture v0.1

- Parent: `SLL-PROP-DATA-001` (frozen) · Boundary: `001A` (frozen; layers L0–L3 and invariants I1–I10 apply unchanged) · Siblings: 001B–001H (this contract observes them, never overrides them)
- Status: **Draft** (2026-09-18). Freezes when §11 holds for four consecutive weeks on the live service.
- Implementation state: §6 watermarks, §7 reconciliation and §8 finding detection implemented **read-only** (`services/lens-api/app/housekeeping/`, `GET /housekeeping/status`, MCP `housekeeping_status`); §9 actions, §10 `housekeeping.*` DDL and the Control Center pending.
- Origin: operator-accepted recommendation (2026-09-18) to add a permanent stewardship control plane around the 001 pipeline rather than more extraction features.

## 1. Purpose

The 001 family says what the data *is*. This contract says how the platform **knows the state of its own data**: what exists, where it came from, what happened to it, whether it is complete, current, reconciled, reviewable, publishable, and when policy retires it. The Housekeeper is the autonomous stewardship layer that answers those questions continuously and acts only within a fixed, safe envelope.

Position statement: *BST Social Lens is an evidence-preserving market-intelligence system. The Housekeeper keeps every piece of its data traceable, valid, current, correctly placed in its lifecycle, reconciled with upstream and downstream evidence, appropriately retained and safe to use — without changing market facts.*

## 2. Scope and non-goals

In scope: pipeline watermarks, reconciliation between layers, findings with a fixed vocabulary, a safe-action policy, lifecycle (freshness) states, retention execution, lineage answers, privacy lifecycle controls, and the operator cockpit that presents them.

Out of scope: market truth (001C–001E own it), publication decisions (001H), the compliance register (parked), any new storage technology. PostgreSQL + PostGIS remain the platform at the current scale; lakehouse/streaming components are revisited only on a measured volume trigger (§12 Q4).

## 3. Layer naming (binding)

001A's layers stay: **L0** raw evidence · **L1** source records + sightings · **L2** intelligence · **L3** published. Within L2 the Housekeeper distinguishes *stages* for watermarks and reconciliation: `extract` → `geo` → `cluster` → `resolve` → `market`; publication is the L3 stage `publish`. Finer layer numbers used in discussion (L2G, L3A/B, L4, L5) map onto these stages and are not introduced as schema or contract terms.

## 4. Authority — what the Housekeeper may and must never do (invariant HK-I1)

| MAY (autonomously) | MUST NOT (without a human decision through 001G review) |
|---|---|
| detect, measure, classify, reconcile | merge or split market properties |
| retry a failed or missing step; re-run a stage whose method version is behind | change or invent asking prices, coordinates, claims, source text |
| recompute derived snapshots (statistics, DQ) — new rows only | overwrite or supersede a `HUMAN` decision (E4) |
| quarantine input that fails validation | delete `protected` evidence or anything referenced by a published version |
| flag, open review work, recommend | reinterpret historical observations (method bump = new rows, old rows retired, never edited) |
| purge exactly what the frozen retention policy (001A D3) says, when due | purge ahead of policy or outside its order (L0 body → L1 rows; L2/L3 never) |

Every Housekeeper action writes one `audit.events` row (actor `housekeeper`, role `system`); a recommended action that is not auto-allowed becomes a finding in state `OPEN` and, where 001G defines a queue, a review item.

## 5. Lineage model (adopted conceptually from W3C PROV / OpenLineage; no new tables in v0.1)

Entities and the activities that produce them already carry identifiers and versions:

```
raw_captures.payload_hash        ── parser (parser_version) ──▶ records.key (+ capture_events per sighting/context)
records.key + content_hash       ── extract.runs (RULE_V1/x.y.z) ──▶ extract.observations.observation_id ▶ claims, price_observations
observation_id                   ── same run (GEO_RULE_V1) ──▶ geo.resolved_locations.resolved_location_id
observation_id                   ── resolution.runs (MATCH_V1) ──▶ resolution.entity_candidates / entity_decisions ▶ market.properties.market_property_id
market_property_id               ── snapshot (stats_version, dq_version) ──▶ market.property_stats_snapshots.snapshot_id
snapshot_id + observation_id     ── publish (001H) ──▶ publish.dataset_version_members
human review                     ── audit.events (reviewer) ──▶ superseding rows in any of the above
```

`GET /lineage/{id}` (§9) walks these edges from any identifier in either direction. A dedicated `housekeeping.lineage_edges` table is added only if a walk cannot be answered from existing keys (decision Q2).

## 6. Watermarks

Per stage (`capture`, `raw`, `ingest`, `extract`, `geo`, `resolve`, `snapshot`, `publish`, `retention`): `last_success_at`, `last_failure_at`, `records_in`, `records_pending`, `oldest_pending_at`, `lag_seconds` (now − oldest pending), `method_version` in force, `behind_version` count (rows produced by an older method). Watermarks are computed from run tables and row timestamps; they are never stored as the only record of a run.

## 7. Reconciliation

Continuous pairwise checks; each yields `(expected, observed, ratio)` and, below threshold, a finding:

| Check | Pair | Finding when broken |
|---|---|---|
| R-EVID | `records.first_payload_hash` ↔ `raw_captures` | `RAW_MISSING` (record without raw) · `RAW_ORPHAN` (raw without record) |
| R-SIGHT | `records` ↔ `capture_events` | `RECORD_WITHOUT_EVENT` |
| R-EXTR | current `records.content_hash` ↔ `extract.current_observations` (method = current) | `EXTRACTION_STALE`, `EXTRACTION_MISSING` |
| R-GEO | observation with location claims ↔ primary `geo.resolved_locations` | `GEO_UNRESOLVED`; `GEO_CONFLICT` when signals say so |
| R-RES | SALE/RENT observation ↔ `resolution.current_decisions` (flag on) | `MATCH_MISSING`, `MATCH_AMBIGUOUS` (REVIEW_REQUIRED older than SLA) |
| R-PROP | decision ↔ ACTIVE `market.properties` with ≥1 current observation | `PROPERTY_EMPTY` (001G exception) |
| R-SNAP | ACTIVE property ↔ snapshot newer than its latest observation | `SNAPSHOT_STALE` |
| R-PUB | published member ↔ eligible snapshot + observation | `PUBLICATION_INELIGIBLE` |
| R-RET | raw bodies past `raw_days`, records past `days` (unprotected) | `RETENTION_OVERDUE`; `PROTECTED_PURGE_ATTEMPT` (must be 0) |

Ratios are reported per check and rolled into one `health` per stage: `healthy` (≥ 0.99), `degraded` (≥ 0.9), `failing`.

## 8. Findings

`finding_type` vocabulary (closed; extend by contract revision): `RAW_MISSING`, `RAW_ORPHAN`, `RECORD_WITHOUT_EVENT`, `QUARANTINED_PAYLOAD`, `EXTRACTION_STALE`, `EXTRACTION_MISSING`, `PARSER_REGRESSION`, `SCHEMA_DRIFT`, `GEO_UNRESOLVED`, `GEO_CONFLICT`, `MATCH_MISSING`, `MATCH_AMBIGUOUS`, `PROPERTY_EMPTY`, `PROPERTY_STALE`, `SNAPSHOT_STALE`, `DQ_REGRESSION`, `PUBLICATION_INELIGIBLE`, `RETENTION_OVERDUE`, `PROTECTED_PURGE_ATTEMPT`, `LINEAGE_BROKEN`, `SYNC_LAG`.

Shape: `finding_id, finding_type, severity ∈ {INFO, WARN, ERROR, CRITICAL}, entity_type, entity_id, detected_at, expected_state, observed_state, recommended_action, auto_action_allowed, status ∈ {OPEN, ACTIONED, REVIEW, RESOLVED, SUPPRESSED}, resolved_at, action_audit_id`. Aggregate findings (one per check per run, with counts) are the default; per-entity findings are materialised only for actionable types.

Quarantine (`QUARANTINED_PAYLOAD`, reason ∈ `HASH_MISMATCH | BODY_TOO_LARGE | JSON_INVALID | SCHEMA_UNKNOWN | PLATFORM_UNKNOWN | PARSER_FAILED | PRIVACY_POLICY | SECURITY_ANOMALY`) keeps the rejected input's hash and context (never the body beyond `LENS_RAW_MAX_BYTES`) so parser drift can be studied; 001B's `rejected_hashes` becomes the first source of these rows.

## 9. Operating loop and surfaces

`OBSERVE → MEASURE → RECONCILE → CLASSIFY → (SAFE ⇒ AUTO-ACTION | else REVIEW) → VERIFY → AUDIT`. Deterministic, scheduled (`LENS_HK_INTERVAL_MIN`, default 30), one run at a time, idempotent; a run that finds nothing writes one `housekeeping.runs` row and no findings.

Safe auto-actions (v0.1): re-run extraction for `EXTRACTION_STALE`; recompute snapshots for `SNAPSHOT_STALE`; execute due retention; re-request raw for `RAW_MISSING` (server marks; the extension re-sends on next sync). Everything else is a finding.

Surfaces: `GET /housekeeping/status` (watermarks, reconciliation, open findings, stage health — implemented), `GET /housekeeping/findings?type=&status=`, `POST /admin/housekeeping/run`, `GET /lineage/{id}`; MCP `housekeeping_status` (implemented), `lineage`. The **Data Control Center** (Portal page, Phase 7) renders these; until then the Console shows the status JSON.

## 10. Lifecycle (freshness) states and physical form

Operational state per observation/property, derived from `last_observed`: `CURRENT` (0–30 d), `AGING` (31–90 d), `STALE` (91–180 d), `HISTORICAL` (> 180 d); plus `WITHDRAWN` (source gone, 001B signal) and `SUPERSEDED` (001F merge). State changes never delete evidence; they gate publication eligibility (001H) and stats windows. Thresholds are a starting policy to be calibrated on Lao market behaviour (Q3).

`housekeeping.*` (draft, unapplied until the read-only surfaces have run for two weeks): `runs`, `findings`, `actions`, `watermarks` (latest per stage, history in `runs`), `quarantine`, `lifecycle_states` (append-only state transitions with reason). Schema-lint rules apply (append-only, `supersedes_*` on state tables, no fact-like columns).

## 11. Acceptance gate (freeze criteria)

1. Watermarks and reconciliation ratios for every stage on the live service, refreshed within the interval, for four weeks without a false `CRITICAL`.
2. Every auto-action has exactly one audit row and is reversible by a superseding row; zero `PROTECTED_PURGE_ATTEMPT`.
3. `GET /lineage/{id}` answers for a payload hash, a record key, an observation, a decision and a property in ≤ 500 ms.
4. Finding counts reconcile with the underlying queries on a disposable copy (CI test on fixtures).
5. Operator can read stage health from the cockpit without SQL.

## 12. Decisions requested (defaults apply per delegation)

| ID | Question | Default |
|---|---|---|
| Q1 | One contract with sections A–F rather than six child contracts? | **Yes** — split a section only when it needs its own freeze cadence |
| Q2 | No `lineage_edges` table in v0.1; lineage walked from existing keys? | **Yes** |
| Q3 | Lifecycle thresholds 30/90/180 days as starting policy, calibrated later? | **Yes** |
| Q4 | Storage stays PostgreSQL + PostGIS until > 5 M observations or analytical queries exceed 2 s p95? | **Yes** |
| Q5 | Housekeeper actions restricted to the §9 safe list; all else findings? | **Yes** |
