# Gate 5 — Lao EDPL 2017 mapping and platform-ToS exposure register

- Status: **Draft for Gate 5 review** (OP-Vily to approve; not legal advice — a Lao-qualified
  reviewer should confirm the EDPL reading before any external distribution of the data)
- Date: 2026-09-17
- Scope: BST Social Lens as built through v0.5.0 + PRs #7 (Layer B) and #8 (Layer C spike)
- Inputs: ADR-0001…0004, `src/lib/types.ts` (SocialRecord v1), `services/lens-api/app/schema.sql`,
  AGENTS.md §3.4–3.6 guardrails

## 1. What the system holds (data inventory)

| Field group | Fields (SocialRecord v1) | Personal data? | Notes |
|---|---|---|---|
| Content | `text`, `hashtags`, `media[].url/thumbnail`, `lang` | Yes when the post text names or describes a person; usually a listing (land/house/vehicle) | Public-post content the operator could read in the feed |
| Identity of author | `author_name`, `author_hash` (SHA-256 of `platform:author_id`), `author_url` | **Yes** — a name plus a profile URL identifies a natural person | `author_id` raw is stored only when `hashAuthorIds` is off (default **on**) |
| Engagement | `reactions_total/breakdown`, `comments_count`, `shares_count`, `views_count` | No (aggregate) | Refreshable by Layer C |
| Context | `permalink`, `container_id/name/type`, `created_at`, `captured_at` | Group name may be personal for small groups | Frontier stores `url_hash` + normalised URL |
| Comments | same as content + `parent_post_id` | Yes (commenter name) | Captured only when `captureComments` is on (default on) |
| Raw payloads | full GraphQL/API JSON bodies | **Yes — may include more than the parsed fields** (profile pictures, mutual-friend hints, reaction lists) | Retained `rawRetentionDays` (30) in the extension only; never sent to lens-api; gitignored in `_live/` |
| Operator config | `ingestToken`, keyword set | Operator's own | Local IndexedDB; token also in `.env` on the server |

Not held by design (AGENTS.md §3.4): member lists, contact numbers, locations of persons,
per-person aggregation across posts. `author_hash` is a pseudonym, not anonymisation — the
same person is linkable across records, which is the point of the hash and also why it stays
personal data.

## 2. Lao Law on Electronic Data Protection No. 25/NA (2017) — mapping

Reading used (secondary sources, see §6): the Law distinguishes **general data** (may circulate
if the source is indicated) from **specific data** (cannot be accessed, used or disclosed
without the information owner's consent; examples in the 2018 Implementing Instructions include
customer information, financial data, medical history, race, religion). The Ministry of
Technology and Communications (MTC, formerly MPT) supervises; LaoCERT responds to incidents.
Data administrators must state purpose, recipients and retention, keep data accurate and
secure, delete on purpose expiry or consent withdrawal, and not transfer data abroad without
the owner's consent. Fines are modest (order of LAK 15 million) but the Penal Code adds
imprisonment for damaging unauthorised disclosure.

| # | Obligation (as read) | BSL today | Gap | Action |
|---|---|---|---|---|
| E1 | Classify data: general vs specific | Content of public listings ≈ general (source = the platform permalink is stored). Author name/URL, and any post text that reveals health, finances, religion, ethnicity ≈ **specific** | No classification at capture; keyword sets target real-estate terms, not sensitive categories | Add a `sensitivity` tag at parse (rule: any of a small Lao/Thai/English sensitive-term list → `specific`) and exclude `specific` records from export/Console by default |
| E2 | Consent of the information owner before collection/use of specific data | None from post authors; the operator is a group member viewing the same content | **Open.** The defensible position is that public listing content with the source indicated is general data used for market intelligence; author identity is minimised to name + hash | Document the lawful-basis reasoning in this register (this row); keep author identity out of the Console leads table (show `author_hash` only) — see A2 |
| E3 | State purpose, recipients, retention | Purpose: Lao real-estate market signal for BST. Recipients: BST internal (lens-api on bizera-wsl, no third parties). Retention: raw 30 days; records indefinite | Records have no retention limit | Set `records` retention (proposal: 24 months, engagement refresh allowed) with a purge job in lens-api; record it in STATUS.md |
| E4 | Accuracy; owner may amend / stop transfer / request deletion | Upsert keeps engagement current | No deletion path by author request | Add `DELETE /records?author_hash=` (token-gated) and a documented request channel (BizEra contact) in the store listing |
| E5 | Security measures appropriate to the technology | lens-db not published; API bearer token; 127.0.0.1 binds; secrets in gitignored `.env`; author ids hashed | Token is static; Console has no per-user auth; laptop IndexedDB unencrypted at rest | Rotate token per deployment; disk encryption on the operator laptop (BitLocker) as a stated control; per-user auth is Phase 4 with the `/mcp` adapter |
| E6 | No cross-border transfer without consent | All storage on bizera-wsl (Laos). Exports are files the operator controls | Any sync to a foreign cloud (e.g. a Claude Project doc, GitHub issue) containing records would be a transfer | **Rule:** never attach record exports or `_live/` payloads to GitHub or cloud docs; sanitised fixtures only (already gitignored) |
| E7 | Breach notification (Cybercrime Law + MPT Recommendation 2543/2018) | None | No incident path | One-paragraph incident procedure in the runbook §10: contain (stop lens-api, revoke token), assess, notify MTC/LaoCERT if the breach is of the notifiable kind |
| E8 | Employee data (2020 guidance) | Not applicable — no employee data | — | — |

**Lawful-basis position (for Gate 5 sign-off):** BSL processes publicly posted commercial
listings, with the source indicated, for the operator's own market-intelligence use, inside
Laos, with author identity minimised to a display name and a pseudonymous hash, no member
aggregation, and no onward disclosure. Sensitive-category content is not targeted and (after
E1) is excluded by default. This is a risk-managed position, not a consent-based one; it must
be re-examined before any of: commercial resale of the data, cross-border hosting, or a
member-profile feature (which ADR-0004 and AGENTS.md already prohibit).

## 3. Platform Terms exposure register

| # | Surface | Term (paraphrased; check current text) | Layer | Exposure | Control |
|---|---|---|---|---|---|
| T1 | Meta Terms of Service §3.2.3 | No automated access or collection of data without permission | A, B (logged-in) | **High** — passive interception and auto-scroll are automated collection by an account holder | Human-paced jitter, caps, panel-open-only, secondary account, no permalink following (ADR-0003/0004). Accept residual risk of account action; never the primary account |
| T2 | Meta Terms — logged-out access | *Meta v. Bright Data*: Terms bind account holders; logged-off public collection was not a breach on that record | C | Medium-low (contract); other laws unaffected | Layer C is logged-out only, boundary-validated, public permalinks only (ADR-0004 rule 3; `validate_target`) |
| T3 | TikTok Terms of Service — automated access | Prohibits scraping / automated data collection without consent | A (logged-in TikTok), C | High (A), medium (C) | Same as T1; TikTok video pages for C are public, anonymous |
| T4 | Chrome Web Store / Edge Add-ons policies | Single purpose; Limited Use of user data; no obfuscation; accurate listing | Store | Medium — a listing that reads as a scraper risks rejection | AGENTS.md §3.6 (no "scraper/crawler" wording); privacy disclosure must state what is collected and that it stays local + operator's own server; consider unlisted/enterprise distribution instead of public store |
| T5 | Facebook group rules | Many Lao property groups forbid data collection in their rules | A, B | Medium — group admin removal | Operator judgement; prefer groups whose rules allow market listings aggregation; never post or interact |

## 4. Store-facing privacy disclosure (draft text)

> BST Social Lens records the public posts and comments you scroll past in Facebook groups and
> TikTok, together with their engagement counts, into a local store on your computer and, if
> you configure it, into a server you control. Author identifiers are stored as one-way hashes.
> No data is sent to the developer. You can export or delete everything from the side panel.

## 5. Risk register (Gate 5)

| ID | Risk | L | I | Owner | Status | Mitigation / evidence |
|---|---|---|---|---|---|---|
| R1 | Account restriction on the secondary account | M | M | OP-Vily | accepted | ADR-0003 caps; A/B evidence on #7 will show observed behaviour |
| R2 | Raw payloads contain more personal data than the schema | H | M | OP-Vily | open → E1/E6 | 30-day purge; never leaves the laptop; sensitivity tag |
| R3 | Records retained indefinitely | H | L | OP-Vily | open → E3 | 24-month retention job |
| R4 | Cross-border leak via docs/issues | M | H | OP-Vily + agents | control | Rule E6; fixtures sanitised; `_live/` gitignored |
| R5 | Static API token compromise | L | M | OP-Vily | control | localhost bind; rotate per deployment |
| R6 | Store rejection / takedown | M | L | OP-Vily | accepted | T4 controls; unlisted distribution fallback |
| R7 | Legal challenge by platform | L | H | OP-Vily | accepted | Layer policy (ADR-0004); no resale; no member aggregation |

## 6. Sources (secondary; verify against the Lao text before reliance)

- DLA Piper, *Data Protection Laws of the World — Laos* (definitions, consent, regulator)
- Tilleke & Gibbins, *Cybersecurity and Data Protection in Mainland Southeast Asia* (2024) — Laos section
- Law on Electronic Data Protection No. 25/NA (2017); Instructions on Implementation (2018); MPT Recommendation No. 2543 (2018)
- *Meta Platforms, Inc. v. Bright Data Ltd.*, No. 3:23-cv-00077 (N.D. Cal. 2024) — see ADR-0004 §Context 2 for the limits of that reading

## 7. Gate 5 exit criteria

Gate 5 closes when OP-Vily approves this register and E1, E3, E4, E7 are tracked as issues
(or implemented). E2/E6 are policy rows: approving the register adopts them.
