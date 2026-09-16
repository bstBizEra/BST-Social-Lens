import { describe, expect, it } from 'vitest';
import { DEFAULT_AUTORUN, nextDelay, shouldStop, type AutoRunState } from '../src/lib/autorun';

const fresh = (): AutoRunState => ({ scrolls: 0, startedAt: 1_000_000, idleRounds: 0 });

describe('nextDelay', () => {
  it('stays within [min, max]', () => {
    for (const r of [0, 0.5, 0.999]) {
      const d = nextDelay(DEFAULT_AUTORUN, () => r);
      expect(d).toBeGreaterThanOrEqual(DEFAULT_AUTORUN.minDelayMs);
      expect(d).toBeLessThanOrEqual(DEFAULT_AUTORUN.maxDelayMs);
    }
  });
});

describe('shouldStop', () => {
  it('does not stop mid-run when the feed keeps growing', () => {
    const s = fresh();
    s.scrolls = 5;
    expect(shouldStop(DEFAULT_AUTORUN, s, true, 1_000_000 + 1000)).toBe(null);
    expect(s.idleRounds).toBe(0);
  });

  it('stops at maxScrolls', () => {
    const s = fresh();
    s.scrolls = DEFAULT_AUTORUN.maxScrolls;
    expect(shouldStop(DEFAULT_AUTORUN, s, true, 1_000_000)).toBe('maxScrolls');
  });

  it('stops at maxMinutes', () => {
    const s = fresh();
    s.scrolls = 1;
    const later = 1_000_000 + DEFAULT_AUTORUN.maxMinutes * 60_000;
    expect(shouldStop(DEFAULT_AUTORUN, s, true, later)).toBe('maxMinutes');
  });

  it('stops after idleRoundsToStop consecutive no-growth rounds', () => {
    const s = fresh();
    s.scrolls = 3;
    const cfg = { ...DEFAULT_AUTORUN, idleRoundsToStop: 3 };
    expect(shouldStop(cfg, s, false, 1_000_001)).toBe(null); // 1
    expect(shouldStop(cfg, s, false, 1_000_002)).toBe(null); // 2
    expect(shouldStop(cfg, s, false, 1_000_003)).toBe('endOfFeed'); // 3
  });

  it('growth resets the idle counter', () => {
    const s = fresh();
    s.scrolls = 3;
    const cfg = { ...DEFAULT_AUTORUN, idleRoundsToStop: 2 };
    shouldStop(cfg, s, false, 1); // idle 1
    shouldStop(cfg, s, true, 2); // reset
    expect(s.idleRounds).toBe(0);
    expect(shouldStop(cfg, s, false, 3)).toBe(null); // idle 1 again, not stop
  });
});
