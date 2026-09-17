import { describe, expect, it } from 'vitest';
import { contentHashInput, decideSighting, normalizeTargetUrl, planRawBatch, sightingContext, truncateUtf8, utf8Length } from '../src/lib/provenance';

describe('contentHashInput', () => {
  const base = { platform: 'facebook' as const, post_id: '1', record_type: 'post' as const, text: 'ຂາຍດິນ', created_at: '2026-09-17T00:00:00Z', permalink: 'https://www.facebook.com/groups/1/posts/1/', media: [{ kind: 'image' as const, url: 'https://x/a.jpg' }], hashtags: ['ດິນ'], author_hash: 'h' };
  it('is stable across engagement changes and NFC forms', () => {
    const a = contentHashInput(base);
    const b = contentHashInput({ ...base, text: base.text.normalize('NFD') });
    expect(a).toBe(b);
  });
  it('changes when content changes', () => {
    expect(contentHashInput({ ...base, text: 'ຂາຍບ້ານ' })).not.toBe(contentHashInput(base));
    expect(contentHashInput({ ...base, media: [] })).not.toBe(contentHashInput(base));
  });
});

describe('planRawBatch', () => {
  const row = (id: number, size: number) => ({ id, body: 'x'.repeat(size) });
  it('bounds by count and bytes, in order', () => {
    const p = planRawBatch([row(1, 100), row(2, 100), row(3, 100), row(4, 100)], 250, 10);
    expect(p.rows.map((r) => r.id)).toEqual([1, 2]);
    expect(p.bytes).toBe(200);
    expect(planRawBatch([row(1, 1), row(2, 1), row(3, 1)], 1000, 2).rows).toHaveLength(2);
  });
  it('reports single rows that can never fit instead of blocking the queue', () => {
    const p = planRawBatch([row(1, 500), row(2, 10)], 100, 10);
    expect(p.skippedTooLarge.map((r) => r.id)).toEqual([1]);
    expect(p.rows.map((r) => r.id)).toEqual([2]);
  });
  it('counts UTF-8 bytes for Lao text', () => {
    expect(utf8Length('ດິນ')).toBe(9);
    expect(utf8Length('a😀')).toBe(5);
    expect(planRawBatch([{ id: 1, body: 'ດິນ' }], 8, 5).skippedTooLarge).toHaveLength(1);
  });
});

describe('truncateUtf8', () => {
  it('leaves short bodies alone', () => {
    expect(truncateUtf8('abc', 10)).toEqual({ text: 'abc', truncated: false });
  });
  it('cuts by UTF-8 bytes, not characters (Lao is 3 bytes/char)', () => {
    const lao = 'ຂາຍດິນ'; // 6 chars, 18 bytes
    const r = truncateUtf8(lao, 7);
    expect(r.truncated).toBe(true);
    expect(r.text).toBe('ຂາ'); // 2 chars = 6 bytes; a third would make 9 > 7
    expect(utf8Length(r.text)).toBeLessThanOrEqual(7);
  });
  it('never splits a surrogate pair', () => {
    const r = truncateUtf8('a😀b', 4); // 'a' (1) + emoji (4) = 5 > 4 → cut before the emoji
    expect(r.text).toBe('a');
    expect(utf8Length(r.text)).toBeLessThanOrEqual(4);
  });
  it('the truncated text is exactly what gets hashed and sent (byte cap == server cap)', () => {
    const body = 'ລາຄາ 2.5 ຕື້ '.repeat(1000);
    const r = truncateUtf8(body, 2000);
    expect(utf8Length(r.text)).toBeLessThanOrEqual(2000);
    expect(utf8Length(r.text)).toBeGreaterThan(1990);
  });
});

describe('sightings (0.7.2): once per context, again on a new context or changed content', () => {
  it('context is the container when known, else host+path without query', () => {
    expect(sightingContext('https://www.facebook.com/groups/123/?sorting=new', '123')).toBe('container:123');
    expect(sightingContext('https://m.facebook.com/groups/123/?sorting=new', undefined)).toBe('page:facebook.com/groups/123');
    expect(sightingContext('https://www.facebook.com/some.page/posts/99?x=1', undefined)).toBe('page:facebook.com/some.page/posts/99');
    expect(sightingContext(undefined, undefined)).toBe('page:unknown');
  });
  it('decides new / repeat / new_context / changed', () => {
    const stored = { content_hash: 'h1', contexts: ['container:123'] };
    expect(decideSighting(undefined, { content_hash: 'h1' }, 'container:123')).toBe('new');
    expect(decideSighting(stored, { content_hash: 'h1' }, 'container:123')).toBe('repeat');
    expect(decideSighting(stored, { content_hash: 'h1' }, 'container:456')).toBe('new_context');
    expect(decideSighting(stored, { content_hash: 'h1' }, 'page:facebook.com/permalink.php')).toBe('new_context');
    expect(decideSighting(stored, { content_hash: 'h2' }, 'container:123')).toBe('changed');
    // rows from before 0.7.2 have no contexts → the first re-sighting counts as a new context (never silently dropped)
    expect(decideSighting({ content_hash: 'h1' }, { content_hash: 'h1' }, 'container:123')).toBe('new_context');
  });
  it('normalises capture-target links and rejects other sites', () => {
    expect(normalizeTargetUrl('https://m.facebook.com/groups/laoland/?ref=share')).toBe('https://www.facebook.com/groups/laoland');
    expect(normalizeTargetUrl('http://fb.com/groups/123456/')).toBe('https://www.facebook.com/groups/123456');
    expect(normalizeTargetUrl('https://www.tiktok.com/tag/ຂາຍດິນ')).toBe('https://www.tiktok.com/tag/%E0%BA%82%E0%BA%B2%E0%BA%8D%E0%BA%94%E0%BA%B4%E0%BA%99');
    expect(normalizeTargetUrl('https://example.com/groups/1')).toBeNull();
    expect(normalizeTargetUrl('not a url')).toBeNull();
  });
});
