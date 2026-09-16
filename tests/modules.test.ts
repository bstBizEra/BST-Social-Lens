import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { facebookModule } from '../src/lib/modules/facebook';
import { tiktokModule } from '../src/lib/modules/tiktok';
import { moduleForResponse } from '../src/lib/modules';
import type { ParseContext } from '../src/lib/modules/types';

const ctx = (page_url: string, hash = true): ParseContext => ({
  page_url,
  captured_at: '2026-09-16T00:00:00.000Z',
  hashAuthorIds: hash,
  sha256: async (s) => `sha:${s}`,
});
const fx = (n: string) => readFileSync(new URL(`./fixtures/${n}`, import.meta.url), 'utf8');

describe('registry', () => {
  it('routes facebook graphql and tiktok api', () => {
    expect(moduleForResponse('https://www.facebook.com/api/graphql/', 'https://www.facebook.com/groups/x')?.platform).toBe('facebook');
    expect(moduleForResponse('https://www.tiktok.com/api/post/item_list/?x=1', 'https://www.tiktok.com/@laocafe')?.platform).toBe('tiktok');
    expect(moduleForResponse('https://www.facebook.com/ajax/x', 'https://www.facebook.com/')).toBeUndefined();
  });
});

describe('facebook module', () => {
  it('parses a group feed with deferred multi-json and merges the richest copy', async () => {
    const recs = await facebookModule.parse(fx('facebook-group-feed.json'), ctx('https://www.facebook.com/groups/laocoffee'));
    expect(recs).toHaveLength(1);
    const r = recs[0]!;
    expect(r.key).toBe('facebook:1234567890123456');
    expect(r.container_id).toBe('laocoffee');
    expect(r.author_name).toBe('Somphone K.');
    expect(r.author_id).toBeUndefined();
    expect(r.author_hash).toBe('sha:facebook:100001234567890');
    expect(r.text).toContain('ອາຣາບິກາ');
    expect(r.hashtags).toEqual(['laocoffee', 'arabica']);
    expect(r.reactions_total).toBe(42);
    expect(r.comments_count).toBe(7);
    expect(r.shares_count).toBe(3);
    expect(r.media[0]).toEqual({ kind: 'image', url: 'https://scontent.example/photo1.jpg' });
    expect(r.created_at).toBe('2026-09-16T00:00:00.000Z');
  });
  it('returns [] on garbage', async () => {
    expect(await facebookModule.parse('<html>', ctx('https://www.facebook.com/groups/x'))).toEqual([]);
  });
});

describe('tiktok module', () => {
  it('parses item_list with stats, hashtags and media', async () => {
    const recs = await tiktokModule.parse(fx('tiktok-item-list.json'), ctx('https://www.tiktok.com/tag/laocoffee', false));
    expect(recs).toHaveLength(1);
    const r = recs[0]!;
    expect(r.key).toBe('tiktok:7412345678901234567');
    expect(r.container_id).toBe('tag:laocoffee');
    expect(r.author_id).toBe('66123');
    expect(r.permalink).toBe('https://www.tiktok.com/@laocafe/video/7412345678901234567');
    expect(r.views_count).toBe(45000);
    expect(r.reactions_total).toBe(1200);
    expect(r.hashtags).toEqual(['laocoffee', 'vientiane']);
    expect(r.media[0]?.kind).toBe('video');
    expect(r.lang).toBe('lo');
  });
});
