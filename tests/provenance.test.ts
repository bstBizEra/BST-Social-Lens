import { describe, expect, it } from 'vitest';
import { contentHashInput, planRawBatch, utf8Length } from '../src/lib/provenance';

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
