# ADR-0003: Autonomous mode (auto-scroll)

- Status: Accepted
- Date: 2026-09-16
- Deciders: OP-Vily
- SDLC gate: 3/4 (Architecture / Detailed Solution Design)

## Context

Through v0.3.0 the extension was manual/passive: it captured only what the operator scrolled past, with no control to "proceed." Requirement: support both a manual mode and an **autonomous** mode with a Start button, without violating the account-safety rules (jittered pacing, per-run caps, run only while the panel is open, no hidden background browsing).

## Decision

Add an **auto-scroll** autonomous mode that automates page scrolling only — it does not click, navigate, or open links.

- **`src/lib/autorun.ts`** — pure pacing/stop logic: `nextDelay()` (jittered delay in [min,max]) and `shouldStop()` (caps on scrolls and minutes, plus end-of-feed detection via consecutive no-growth rounds). Unit-tested; no DOM/timers.
- **`autoscroll.content.ts`** (isolated world, `document_idle`) — driven by `autoStart`/`autoStop` messages. Scrolls to the bottom with smooth behavior, waits ~1.2s for lazy-load, evaluates growth, and continues after a jittered delay. The MAIN-world interceptor captures the payloads the scrolling triggers — no new capture path. Pauses (not stops) while the tab is hidden; stops on `pagehide`.
- **Background** relays `autoStart`/`autoStop` to the active tab via `chrome.tabs.sendMessage` and holds the latest `autoProgress` (ephemeral) for the side panel to poll via `autoState`.
- **Side panel** — an Autonomous card: Start/Stop, live scroll count, max-scrolls / max-minutes caps, and the stop reason. Manual mode remains the default; both coexist.

Caps default to 40 scrolls / 10 minutes, pacing 2.5–5s. Runs only while the side panel is open and only on the tab the operator is viewing.

## Consequences

Positive: the operator gets a one-click "proceed" that fills the feed hands-free while keeping capture, keyword filtering, and the seen frontier unchanged; safety limits are enforced in one tested module. Manual mode is untouched.

Negative / follow-ups: auto-scroll still can't reach content behind a click (e.g. "view more comments") — full comment coverage and permalink-following remain the Phase-4 assisted-navigation step on the seen frontier, which carries more account risk and is deliberately not in this release. If the tab is backgrounded the run pauses rather than progressing.

## Alternatives considered

- **Background/headless auto-run** — rejected: violates the "no hidden browsing, run only while panel open" safety rule and raises account-lock risk.
- **Auto-open matched permalinks now** — deferred to Phase 4; higher risk, needs the assisted-navigation design.
- **Fixed-interval scrolling** — rejected in favour of jittered pacing to mimic a human.
