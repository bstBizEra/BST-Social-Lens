/**
 * MAIN-world interceptor.
 *
 * Runs inside the page's own JS context (not the isolated content-script world)
 * at document_start, so it can wrap `window.fetch` and `XMLHttpRequest` before
 * the site's code runs. Matching responses are cloned and posted to the
 * isolated-world bridge with window.postMessage. Nothing here touches
 * extension APIs — MAIN world has none.
 *
 * Why this and not chrome.webRequest: on Chrome/Edge MV3, webRequest cannot
 * read response bodies (Firefox's filterResponseData has no equivalent).
 */
import { PAGE_MESSAGE_SOURCE, type PageCaptureMessage } from '../lib/types';

const INTEREST = [
  /\/api\/graphql\/?/, // Facebook
  /\/api\/(post|challenge|recommend|related|user)\/item_list\//, // TikTok lists
  /\/api\/search\/(item|general)\/full\//, // TikTok search
  /\/api\/comment\/list\//, // TikTok comments
];

const MAX_BODY = 4_000_000; // hard cap before the bridge/background trims further

export default defineContentScript({
  matches: ['https://*.facebook.com/*', 'https://*.tiktok.com/*'],
  runAt: 'document_start',
  world: 'MAIN',
  main() {
    const w = window as Window & { __bstSocialLens?: boolean };
    if (w.__bstSocialLens) return; // double-injection guard
    w.__bstSocialLens = true;

    const interesting = (url: string) => INTEREST.some((re) => re.test(url));

    const post = (payload: PageCaptureMessage['payload']) => {
      const msg: PageCaptureMessage = { source: PAGE_MESSAGE_SOURCE, type: 'capture', payload };
      window.postMessage(msg, window.location.origin);
    };

    /* ---- fetch ---- */
    const origFetch = window.fetch.bind(window);
    window.fetch = async function (input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
      const res = await origFetch(input, init);
      try {
        const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        if (interesting(url)) {
          const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
          res
            .clone()
            .text()
            .then((body) => {
              if (body.length > MAX_BODY) body = body.slice(0, MAX_BODY);
              post({ url, method, status: res.status, kind: 'fetch', body, page_url: location.href, ts: Date.now() });
            })
            .catch(() => {});
        }
      } catch {
        /* never break the page */
      }
      return res;
    };

    /* ---- XMLHttpRequest ---- */
    const XHR = XMLHttpRequest.prototype;
    const origOpen = XHR.open;
    const origSend = XHR.send;
    type Tagged = XMLHttpRequest & { __bstUrl?: string; __bstMethod?: string };

    XHR.open = function (this: Tagged, method: string, url: string | URL, ...rest: unknown[]) {
      this.__bstUrl = typeof url === 'string' ? url : url.href;
      this.__bstMethod = method.toUpperCase();
      // @ts-expect-error — forwarding variadic args to the native signature
      return origOpen.call(this, method, url, ...rest);
    };

    XHR.send = function (this: Tagged, body?: Document | XMLHttpRequestBodyInit | null) {
      const url = this.__bstUrl ?? '';
      if (interesting(url)) {
        this.addEventListener('load', () => {
          try {
            if (this.responseType !== '' && this.responseType !== 'text') return;
            let text = this.responseText;
            if (text.length > MAX_BODY) text = text.slice(0, MAX_BODY);
            post({ url, method: this.__bstMethod ?? 'GET', status: this.status, kind: 'xhr', body: text, page_url: location.href, ts: Date.now() });
          } catch {
            /* ignore */
          }
        });
      }
      return origSend.call(this, body);
    };

    /* ---- Embedded state (TikTok SSR) ---- */
    const readEmbedded = () => {
      const el = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');
      if (el?.textContent) {
        post({
          url: 'embedded:__UNIVERSAL_DATA_FOR_REHYDRATION__',
          method: 'GET',
          status: 200,
          kind: 'embedded',
          body: el.textContent.slice(0, MAX_BODY),
          page_url: location.href,
          ts: Date.now(),
        });
      }
    };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', readEmbedded, { once: true });
    } else {
      readEmbedded();
    }
  },
});
