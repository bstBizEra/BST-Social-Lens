/**
 * Autonomous auto-scroll (isolated world).
 *
 * Driven by messages from the side panel (via the background worker). Scrolls
 * the page with human-like jitter so the site lazy-loads more content, which
 * the MAIN-world interceptor captures. Stops at caps, at end-of-feed, when the
 * tab is hidden, or on an explicit stop. Never navigates or clicks — scroll only.
 */
import { DEFAULT_AUTORUN, nextDelay, shouldStop, type AutoRunConfig, type AutoRunState } from '../lib/autorun';

type Progress = { running: boolean; scrolls: number; reason: string | null };

export default defineContentScript({
  matches: ['https://*.facebook.com/*', 'https://*.tiktok.com/*'],
  runAt: 'document_idle',
  main() {
    let running = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let state: AutoRunState;
    let cfg: AutoRunConfig = DEFAULT_AUTORUN;

    const report = (reason: string | null) => {
      const p: Progress = { running, scrolls: state?.scrolls ?? 0, reason };
      browser.runtime.sendMessage({ type: 'autoProgress', progress: p }).catch(() => {});
    };

    function stop(reason: string | null) {
      running = false;
      if (timer) clearTimeout(timer);
      timer = undefined;
      report(reason);
    }

    function step() {
      if (!running) return;
      // Pause (don't stop) while the tab is hidden — resume when visible.
      if (document.hidden) {
        timer = setTimeout(step, 1500);
        return;
      }
      const before = document.documentElement.scrollHeight;
      window.scrollTo({ top: before, behavior: 'smooth' });
      state.scrolls += 1;

      // Give the site a moment to lazy-load, then evaluate growth.
      setTimeout(() => {
        if (!running) return;
        const after = document.documentElement.scrollHeight;
        const reason = shouldStop(cfg, state, after > before + 50);
        report(null);
        if (reason) {
          stop(reason);
          return;
        }
        timer = setTimeout(step, nextDelay(cfg));
      }, 1200);
    }

    function start(config?: Partial<AutoRunConfig>) {
      if (running) return;
      cfg = { ...DEFAULT_AUTORUN, ...(config ?? {}) };
      state = { scrolls: 0, startedAt: Date.now(), idleRounds: 0 };
      running = true;
      report(null);
      step();
    }

    browser.runtime.onMessage.addListener((msg: { type?: string; config?: Partial<AutoRunConfig> }) => {
      if (msg?.type === 'autoStart') start(msg.config);
      else if (msg?.type === 'autoStop') stop('stopped');
      else if (msg?.type === 'autoStatus') report(null);
    });

    // Safety: stop if the page is being unloaded.
    window.addEventListener('pagehide', () => stop('stopped'), { once: true });
  },
});
