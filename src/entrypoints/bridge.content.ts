/**
 * Isolated-world bridge.
 *
 * Listens for postMessage events from the MAIN-world interceptor, validates
 * origin + shape, and forwards them to the background service worker over
 * chrome.runtime. Also shows a small floating badge with the live capture
 * count so the operator can see the extension is working on the page.
 */
import { PAGE_MESSAGE_SOURCE, type PageCaptureMessage, type RuntimeMessage } from '../lib/types';

export default defineContentScript({
  matches: ['https://*.facebook.com/*', 'https://*.tiktok.com/*'],
  runAt: 'document_start',
  main() {
    let forwarded = 0;
    let stored = 0;
    let badge: HTMLDivElement | undefined;

    const renderBadge = () => {
      if (!badge) {
        badge = document.createElement('div');
        badge.id = 'bst-social-lens-badge';
        Object.assign(badge.style, {
          position: 'fixed',
          right: '16px',
          bottom: '16px',
          zIndex: '2147483647',
          padding: '6px 10px',
          borderRadius: '999px',
          font: '600 12px/1.2 system-ui, -apple-system, Segoe UI, sans-serif',
          color: '#fff',
          background: 'linear-gradient(135deg,#0f766e,#1d4ed8)',
          boxShadow: '0 4px 14px rgba(0,0,0,.25)',
          pointerEvents: 'none',
          opacity: '0.92',
        } satisfies Partial<CSSStyleDeclaration>);
        (document.body ?? document.documentElement).appendChild(badge);
      }
      badge.textContent = `Social Lens · ${stored} records · ${forwarded} payloads`;
    };

    window.addEventListener('message', (ev: MessageEvent) => {
      if (ev.source !== window || ev.origin !== window.location.origin) return;
      const data = ev.data as Partial<PageCaptureMessage> | undefined;
      if (!data || data.source !== PAGE_MESSAGE_SOURCE || data.type !== 'capture' || !data.payload) return;
      forwarded++;
      const msg: RuntimeMessage = { type: 'capture', payload: data.payload };
      browser.runtime
        .sendMessage(msg)
        .then((res: unknown) => {
          const r = res as { stored?: number } | undefined;
          if (r && typeof r.stored === 'number') stored += r.stored;
          renderBadge();
        })
        .catch(() => {
          /* service worker may be restarting; message is lost but the page keeps working */
        });
    });

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', renderBadge, { once: true });
    } else {
      renderBadge();
    }
  },
});
