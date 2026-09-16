# ADR-0004: Capture layers — extension-first, headless logged-out only

- Status: Accepted
- Date: 2026-09-16
- Deciders: OP-Vily
- SDLC gate: 3 (Architecture Design)
- Supersedes: nothing. Extends ADR-0003 (autonomous mode) and ADR-0002 (seen frontier).

## Context

After v0.5.0 the question was raised whether BST Social Lens should "design scraping
tools in" — i.e. adopt a general-purpose crawler framework or a headless-browser fleet
to widen coverage of Facebook groups and TikTok. A landscape review (2026-09) of the
open-source scraping ecosystem found:

| Approach | Facebook (2026) | TikTok (2026) |
|---|---|---|
| Logged-in browser extension with network interception (what BSL does) | Works — sees the same GraphQL the feed renders | Works — 65 records from a live Lao video in the v0.5.0 smoke test |
| HTTP / mbasic scrapers (`kevinzg/facebook-scraper`, `moda20/facebook-scraper`) | Last push mid-2024; hundreds of open "empty output" issues | `drawrowfly/tiktok-scraper` dead since 2023 |
| Unofficial API wrappers (`davidteather/TikTok-Api`) | n/a | Alive, but needs Playwright + proxies; blocked requests are routine |
| Headless stealth browsers (nodriver, Patchright, Camoufox) | Fingerprinted at login; account bans | 81–90 % pass on Cloudflare benchmarks, which are weaker than Meta/TikTok defences |
| Crawler frameworks (Scrapy, Crawlee, Crawl4AI, Scrapling) | Not built for authenticated social feeds | same |

Two constraints shape the decision:

1. **Account safety** (AGENTS.md §3.5): no hidden background browsing, jittered pacing,
   per-run caps, runs only while the panel is open. Every headless approach violates
   this if it uses a logged-in account.
2. **Legal posture (risk context, not authorisation).** In *Meta Platforms, Inc. v.
   Bright Data Ltd.* (N.D. Cal. 2024, summary judgment) the court held, on the record
   and the contractual terms before it, that Bright Data's logged-off collection of
   publicly available Facebook/Instagram data did not breach the applicable Meta Terms,
   because those Terms bind account holders using the service. That is a case-specific
   contract ruling: it does not make scraping lawful in general, and it leaves
   copyright, privacy, computer-access statutes, platform enforcement, jurisdiction
   (Lao EDPL 2017 applies to us regardless) and the facts of any future dispute as
   separate questions. Automated collection *while logged in* remains prohibited by
   Meta's Terms. The ADR uses the case only to rank the layers by contractual exposure;
   the data-minimisation guardrails (post content and engagement only, author ids
   hashed, no member-profile aggregation) apply to every layer.

## Decision

Capture is organised in three layers. Layers are additive; each has a fixed risk
envelope and a fixed place in the codebase.

```
Layer A  Extension capture         operator's own browser, logged in, passive interceptor
         (v0.1 → v0.5, ADR-0001)   + auto-scroll (ADR-0003)              → lens-api /ingest

Layer B  Assisted navigation       same tab, same session: expands "view more comments"
         (v0.6, this ADR)          and "view replies" with jittered pacing; no navigation
                                   away from the page; runs only while the panel is open

Layer C  Headless worker           bizera-wsl service, LOGGED OUT, public permalinks from
         (spike, services/         the seen frontier only; curl_cffi first, nodriver
          lens-worker)             fallback; never touches a logged-in feed
```

### Rules

1. **Layer A is the product.** Coverage improvements go into the extension first.
   It is the only path validated on live Lao content and the only one with no
   fingerprint or contractual exposure beyond ordinary use of an account.
2. **Layer B may click, never navigate.** Allowed targets are comment-thread expanders on
   the page the operator is viewing. Opening permalinks, following links, or changing
   `location` is out of scope for Layer B and would require a new ADR.
3. **Layer C is logged-out only.** The worker carries no cookies, no session, no
   credentials. It consumes URLs from the seen frontier (`last_status = 'seen'`), fetches
   the public permalink, and reports `fetched` / `failed` / `skipped` back to `/seen`.
   If a target requires login it is marked `skipped`, not retried with a session.
4. **One headless runtime.** Python (matches lens-api). `curl_cffi` (Chrome-TLS HTTP)
   is the primary fetcher; `nodriver` (raw CDP, no Playwright handshake) is the fallback
   for JS-rendered pages. Crawlee/Playwright/Scrapy are **not** adopted; Crawl4AI is
   noted as a candidate for a *different* BST product (open-web Lao news/listing ingest),
   not for BSL.
5. **Go/no-go on evidence.** Layer C graduates from spike to service only if the block
   rate on a 50-URL frontier sample is ≤ 20 % and it adds fields the extension does not
   already fill (e.g. engagement refresh on older posts). Otherwise it is archived.
6. **Store-listing language unchanged.** Layers B and C do not change manifest or
   store copy; "scraper"/"crawler" remain prohibited in store-facing text.
7. **Parser health is monitored.** Because Meta and TikTok rotate payload shapes within
   weeks, a scheduled CI job replays fixtures through the parsers and fails on fill-rate
   regression, so churn surfaces before the Console shows empty KPIs.

## Consequences

Positive
- Coverage grows (comment threads, engagement refresh) without adding a logged-in
  automation surface; the account-safety rules in AGENTS.md remain true as written.
- Contractual exposure is ranked explicitly: Layers A/B are ordinary account use by the
  operator under the Terms; Layer C avoids the logged-in Terms entirely by design. This
  lowers, but does not remove, legal risk — see Context §2.
- A single Python worker runtime keeps operational load on `bizera-wsl` small.

Negative / accepted
- Layer C cannot see group-only content (most Lao property groups are private or
  members-only); its value is limited to public pages, public groups, and TikTok
  video pages. This is accepted — the extension covers the rest.
- nodriver and curl_cffi are community-maintained; a break in either pauses Layer C,
  not the product.
- Comment expansion (Layer B) increases per-run request volume; caps default low
  (see ADR-0003 caps + `assist.maxClicks`).

## Alternatives considered

- **Adopt Crawlee (TS) as a server-side crawler with a logged-in secondary account** —
  rejected: highest ban risk, contractual exposure under Meta's Terms, and a second
  runtime to maintain.
- **Fork `TikTok-Api` / `facebook-scraper`** — rejected: both depend on continually
  reverse-engineered signing/endpoints and are behind the platforms' change cadence.
  `TikTok-Api` is kept as a *reference* for TikTok signing changes only.
- **Do nothing (Layer A only)** — rejected: comment coverage is the largest fill-rate
  gap in the live smoke tests, and it is reachable safely from inside the tab.

## References

- ADR-0001 topology, ADR-0002 keyword/comments/seen frontier, ADR-0003 autonomous mode
- Open-source scraper landscape 2026: Scrapfly "10 Best Open-Source Web Scrapers";
  anti-detect benchmark (7 tools, 31 targets, 651 verdicts), ianlpaterson.com
- *Meta Platforms, Inc. v. Bright Data Ltd.*, No. 3:23-cv-00077 (N.D. Cal.), order on summary judgment, 2024 — cited as risk context only; not legal advice
