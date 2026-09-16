/**
 * Autonomous auto-scroll + assisted navigation (isolated world).
 *
 * Driven by messages from the side panel (via the background worker). Scrolls
 * the page with human-like jitter so the site lazy-loads more content, which
 * the MAIN-world interceptor captures. Stops at caps, at end-of-feed, when the
 * tab is hidden, or on an explicit stop.
 *
 * Layer B (ADR-0004): when `assist.enabled`, after each scroll round it clicks
 * a few in-page expanders ("View more comments", "View N replies", "See more")
 * chosen by the pure allow-list in `lib/assist.ts`. It never navigates — any
 * element whose href leaves the current page is skipped, and a URL change
 * during the run stops it.
 */
import { DEFAULT_AUTORUN, nextDelay, shouldStop, type AutoRunConfig, type AutoRunState } from '../lib/autorun';
import { DEFAULT_ASSIST, assistExhausted, nextClickDelay, pickExpanders, type AssistConfig, type Candidate } from '../lib/assist';

type Progress = { running: boolean; scrolls: number; clicks: number; reason: string | null };

const CANDIDATE_SELECTOR = [
  'div[role="button"]',
  'span[role="button"]',
  'a[role="button"]',
  'button',
  '[data-e2e^="view-more"]',
  '[data-e2e*="expand"]',
].join(',');

export default defineContentScript({
  matches: ['https://*.facebook.com/*', 'https://*.tiktok.com/*'],
  runAt: 'document_idle',
  main() {
    let running = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let state: AutoRunState;
    let cfg: AutoRunConfig = DEFAULT_AUTORUN;
    let assist: AssistConfig = DEFAULT_ASSIST;
    let clicks = 0;
    let startHref = '';
    // Elements already clicked this run — never click the same expander twice.
    const clicked = new WeakSet<Element>();

    const report = (reason: string | null) => {
      const p: Progress = { running, scrolls: state?.scrolls ?? 0, clicks, reason };
      browser.runtime.sendMessage({ type: 'autoProgress', progress: p }).catch(() => {});
    };

    function stop(reason: string | null) {
      running = false;
      if (timer) clearTimeout(timer);
      timer = undefined;
      report(reason);
    }

    /** Guard: the run must stay on the page it started on (SPA pushState included). */
    function navigated(): boolean {
      return location.href.split('#')[0] !== startHref;
    }

    function isVisible(el: Element): boolean {
      const r = (el as HTMLElement).getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return false;
      // Only what is on or just below the viewport — like a person reading down the feed.
      return r.bottom > -200 && r.top < window.innerHeight + 600;
    }

    /** Project DOM elements into DOM-free candidates for the pure picker. */
    function collectCandidates(): { els: Element[]; cands: Candidate[] } {
      const els: Element[] = [];
      const cands: Candidate[] = [];
      for (const el of document.querySelectorAll(CANDIDATE_SELECTOR)) {
        if (clicked.has(el) || !isVisible(el)) continue;
        const text = (el.textContent ?? '').trim();
        if (!text || text.length > 80) continue;
        const anchor = el.closest('a[href]') as HTMLAnchorElement | null;
        els.push(el);
        cands.push({
          text,
          href: anchor?.getAttribute('href') ?? undefined,
          role: el.getAttribute('role') ?? undefined,
          tag: el.tagName.toLowerCase(),
          hint: [el.getAttribute('data-e2e'), el.getAttribute('aria-label')].filter(Boolean).join(' ').toLowerCase() || undefined,
        });
      }
      return { els, cands };
    }

    /** Click the picked expanders one at a time with jitter, then call `done`. */
    function expandRound(done: () => void) {
      if (!running || !assist.enabled || assistExhausted(assist, clicks) || navigated()) return done();
      const { els, cands } = collectCandidates();
      const picks = pickExpanders(cands, location.href, assist, clicks);
      if (picks.length === 0) return done();
      let i = 0;
      const next = () => {
        if (!running || i >= picks.length || navigated()) return done();
        const el = els[picks[i]!.index]!;
        i++;
        try {
          if (!clicked.has(el) && el.isConnected && isVisible(el)) {
            clicked.add(el);
            (el as HTMLElement).click();
            clicks++;
            report(null);
          }
        } catch {
          /* never let a click error escape into the page */
        }
        timer = setTimeout(next, nextClickDelay(assist));
      };
      next();
    }

    function step() {
      if (!running) return;
      // Pause (don't stop) while the tab is hidden — resume when visible.
      if (document.hidden) {
        timer = setTimeout(step, 1500);
        return;
      }
      if (navigated()) return stop('navigated');
      const before = document.documentElement.scrollHeight;
      window.scrollTo({ top: before, behavior: 'smooth' });
      state.scrolls += 1;

      // Give the site a moment to lazy-load, then expand threads, then evaluate growth.
      setTimeout(() => {
        if (!running) return;
        expandRound(() => {
          if (!running) return;
          const after = document.documentElement.scrollHeight;
          const reason = shouldStop(cfg, state, after > before + 50);
          report(null);
          if (reason) {
            stop(reason);
            return;
          }
          timer = setTimeout(step, nextDelay(cfg));
        });
      }, 1200);
    }

    function start(config?: Partial<AutoRunConfig>, assistCfg?: Partial<AssistConfig>) {
      if (running) return;
      cfg = { ...DEFAULT_AUTORUN, ...(config ?? {}) };
      assist = { ...DEFAULT_ASSIST, ...(assistCfg ?? {}) };
      state = { scrolls: 0, startedAt: Date.now(), idleRounds: 0 };
      clicks = 0;
      startHref = location.href.split('#')[0]!;
      running = true;
      report(null);
      step();
    }

    browser.runtime.onMessage.addListener((msg: { type?: string; config?: Partial<AutoRunConfig>; assist?: Partial<AssistConfig> }) => {
      if (msg?.type === 'autoStart') start(msg.config, msg.assist);
      else if (msg?.type === 'autoStop') stop('stopped');
      else if (msg?.type === 'autoStatus') report(null);
    });

    // Safety: stop if the page is being unloaded.
    window.addEventListener('pagehide', () => stop('stopped'), { once: true });
  },
});
