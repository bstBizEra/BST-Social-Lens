import { describe, expect, it } from 'vitest';
import { normalizeUrl, urlHash } from '../src/lib/url';

describe('normalizeUrl', () => {
  it('strips fbclid and other tracking params', () => {
    const a = normalizeUrl('https://www.facebook.com/groups/123/posts/456/?fbclid=XYZ&__cft__[0]=abc');
    expect(a).toBe('https://www.facebook.com/groups/123/posts/456/');
  });

  it('canonicalizes permalink vs posts form to the same URL', () => {
    const a = normalizeUrl('https://www.facebook.com/groups/123/permalink/456/');
    const b = normalizeUrl('https://m.facebook.com/groups/123/posts/456');
    expect(a).toBe(b);
  });

  it('orders story.php identity params stably', () => {
    const a = normalizeUrl('https://www.facebook.com/story.php?story_fbid=9&id=7&fbclid=Z');
    const b = normalizeUrl('https://www.facebook.com/story.php?id=7&story_fbid=9');
    expect(a).toBe(b);
  });

  it('upgrades http, lowercases host, drops fragment', () => {
    const a = normalizeUrl('http://WWW.TikTok.com/@user/video/1#comments');
    expect(a.startsWith('https://www.tiktok.com/@user/video/1')).toBe(true);
    expect(a.includes('#')).toBe(false);
  });

  it('is stable under query-param reordering', () => {
    expect(normalizeUrl('https://x.com/a?b=2&a=1')).toBe(normalizeUrl('https://x.com/a?a=1&b=2'));
  });

  it('returns input on unparseable strings', () => {
    expect(normalizeUrl('not a url')).toBe('not a url');
  });
});

describe('urlHash', () => {
  it('is identical for two links to the same post', async () => {
    const a = await urlHash('https://www.facebook.com/groups/123/permalink/456/?fbclid=A');
    const b = await urlHash('https://m.facebook.com/groups/123/posts/456');
    expect(a).toBe(b);
    expect(a).toMatch(/^[0-9a-f]{64}$/);
  });
});
