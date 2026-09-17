# 03-architecture

Gate 3/4 artefacts.

| Doc | Subject |
|---|---|
| `research-brief-base-stack.md` | Base-stack research (WXT MV3, build vs fork) |
| `adr-0001-lensdb-ingest-topology.md` | Extension buffer → lens-api → LensDB topology |
| `adr-0002-keyword-comments-seen-frontier.md` | Keyword sets, comment records, seen-link frontier |
| `adr-0003-autonomous-scroll-mode.md` | Autonomous auto-scroll mode (caps, pacing) |
| `adr-0004-capture-layers.md` | Capture layers: extension-first (A), assisted navigation (B), headless logged-out worker (C) |
| `adr-0005-market-intelligence-platform-roadmap.md` | **Accepted** — adopts SLL-PROP-DATA-001; Phases 5–9 (v0.7 → v1.1): provenance, extraction+geo, entity resolution+Portal, publication, BizProp+ Reference API; exit criteria per phase; closes Phase 4 |
| `SLL-PROP-DATA-001-parent-v0.1.md` | **Frozen parent**: Social Lens as an independent market-intelligence data platform; five contracts C01–C05; L0–L3; decomposition 001A–001H |
| `SLL-PROP-DATA-001-foundation-notes.md` | Design input behind the parent (pipeline stages, entity model, Portal, BizProp+ contract) — verbatim |
| `SLL-PROP-DATA-001A-domain-data-boundary-v0.1.md` | **001A Domain & Data Boundary (frozen v0.1)** — glossary, ownership/SoR, invariants I1–I10, merge/link rules, BizProp+ consumption boundary, tensions with current code, decisions D1–D7 to freeze |
| `SLL-PROP-DATA-001B-capture-provenance-contract-v0.1.md` | **001B Capture & Provenance (implemented v0.1, Phase 5)** — L0 on server (`/raw`), capture events, content/payload hashes, ordered retention, exit evidence |
| `SLL-PROP-DATA-001C-property-extraction-contract-v0.1.md` | **001C Property Extraction (draft for freeze, Phase 6)** — observations, claims with method/confidence/evidence span, signal-class + asset-type taxonomy, keyword groups, RULE_V1 decision table, LAK/m² normalisation, price observations, contact points, run model + API, golden-fixture gate |
| `SLL-PROP-DATA-001D-geo-normalisation-contract-v0.1.md` | **001D Geo-Normalisation (draft for freeze, Phase 6)** — Lao Data Map reference copy (D4, versioned), resolver GEO_RULE_V1 (point path, text path, reconcile), precision semantics, confidence, run model + API, acceptance gate, decisions G1–G5 |
| `SLL-PROP-DATA-001E-entity-resolution-contract-v0.1.md` | **001E Entity Resolution (draft for freeze, Phase 7)** — three dedup levels (source duplicate / listing cluster / market property), permalink-variant rules, cluster signals, blocking, `MATCH_V1` weighted scoring with penalties and thresholds (≥80 / 60–79 / <60), reversible decisions with history, merge/split, advertisers, derived statistics, run model + API, acceptance gate, decisions E1–E6 |
