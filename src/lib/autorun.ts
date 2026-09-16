/**
 * Autonomous-run pacing + stop logic — pure and testable (no DOM/timers here).
 *
 * The auto-scroll content script calls `nextDelay()` between scrolls and
 * `shouldStop()` after each, so the account-safety rules (jitter, caps,
 * end-of-feed detection) live in one place with unit tests.
 */

export interface AutoRunConfig {
  /** Min/max delay between scrolls, ms. Jitter mimics a human. */
  minDelayMs: number;
  maxDelayMs: number;
  /** Hard cap on scroll actions. */
  maxScrolls: number;
  /** Hard cap on wall-clock minutes. */
  maxMinutes: number;
  /** Stop after this many consecutive scrolls with no new content. */
  idleRoundsToStop: number;
}

export const DEFAULT_AUTORUN: AutoRunConfig = {
  minDelayMs: 2500,
  maxDelayMs: 5000,
  maxScrolls: 40,
  maxMinutes: 10,
  idleRoundsToStop: 3,
};

export interface AutoRunState {
  scrolls: number;
  startedAt: number; // epoch ms
  idleRounds: number; // consecutive no-growth rounds
}

export type StopReason = null | 'maxScrolls' | 'maxMinutes' | 'endOfFeed';

/** Random delay in [min, max], integer ms. `rnd` injectable for tests. */
export function nextDelay(cfg: AutoRunConfig, rnd: () => number = Math.random): number {
  const lo = Math.min(cfg.minDelayMs, cfg.maxDelayMs);
  const hi = Math.max(cfg.minDelayMs, cfg.maxDelayMs);
  return Math.round(lo + rnd() * (hi - lo));
}

/**
 * Decide whether to stop. `grew` = did the page height increase since the last
 * scroll. Mutates `state.idleRounds`; caller updates scrolls/time.
 */
export function shouldStop(cfg: AutoRunConfig, state: AutoRunState, grew: boolean, now: number = Date.now()): StopReason {
  state.idleRounds = grew ? 0 : state.idleRounds + 1;
  if (state.scrolls >= cfg.maxScrolls) return 'maxScrolls';
  if (now - state.startedAt >= cfg.maxMinutes * 60_000) return 'maxMinutes';
  if (state.idleRounds >= cfg.idleRoundsToStop) return 'endOfFeed';
  return null;
}
