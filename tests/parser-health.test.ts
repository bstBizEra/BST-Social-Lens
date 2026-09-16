/**
 * Parser health — fixture replay with fill-rate thresholds.
 *
 * Runs every committed fixture (tests/fixtures/*.json) and any live exports
 * (tests/fixtures/_live/*.ndjson, git-ignored) through the platform modules
 * and fails when a field's fill rate drops below tests/parser-health.thresholds.json.
 * Prints a per-platform table so a regression is visible in CI logs.
 *
 * Meta/TikTok rotate payload shapes within weeks; this catches *code*
 * regressions on every PR and, with fresh live exports dropped into _live/,
 * *platform* regressions on the weekly run (see .github/workflows/parser-health.yml).
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { facebookModule } from '../src/lib/modules/facebook';
import { tiktokModule } from '../src/lib/modules/tiktok';
import { moduleForResponse } from '../src/lib/modules';
import type { Platform, RawPayload, SocialRecord } from '../src/lib/types';

type Thresholds = { min_records: Record<Platform, number> } & Record<Platform, Record<string, number>>;
const thresholds = JSON.parse(readFileSync(new URL('./parser-health.thresholds.json', import.meta.url), 'utf8')) as Thresholds;

const fxDir = new URL('./fixtures/', import.meta.url);
const liveDir = new URL('./fixtures/_live/', import.meta.url);
const ctx = (page_url: string) => ({ page_url, captured_at: '2026-09-16T00:00:00.000Z', hashAuthorIds: true, captureComments: true, sha256: async (s: string) => `sha:${s}` });

// Fixture → the page it was captured on (drives container resolution + routing).
const FIXTURE_PAGES: Record<string, { platform: Platform; page_url: string }> = {
  'facebook-group-feed.json': { platform: 'facebook', page_url: 'https://www.facebook.com/groups/laocoffee' },
  'facebook-groups-feed-comet.json': { platform: 'facebook', page_url: 'https://www.facebook.com/groups/feed/' },
  'tiktok-item-list.json': { platform: 'tiktok', page_url: 'https://www.tiktok.com/tag/laocoffee' },
};

const filled = (r: SocialRecord, k: string) => {
  const v = (r as unknown as Record<string, unknown>)[k];
  return Array.isArray(v) ? v.length > 0 : v !== undefined && v !== null && v !== '';
};

async function replay(): Promise<Record<Platform, SocialRecord[]>> {
  const out: Record<Platform, SocialRecord[]> = { facebook: [], tiktok: [] };
  for (const f of readdirSync(fxDir).filter((f) => f.endsWith('.json'))) {
    const meta = FIXTURE_PAGES[f];
    if (!meta) throw new Error(`fixture ${f} has no FIXTURE_PAGES entry — add one so it is replayed`);
    const mod = meta.platform === 'facebook' ? facebookModule : tiktokModule;
    out[meta.platform].push(...(await mod.parse(readFileSync(new URL(f, fxDir), 'utf8'), ctx(meta.page_url))));
  }
  if (existsSync(liveDir)) {
    for (const f of readdirSync(liveDir).filter((f) => f.endsWith('.ndjson'))) {
      for (const line of readFileSync(new URL(f, liveDir), 'utf8').trim().split('\n')) {
        const p = JSON.parse(line) as RawPayload;
        const mod = moduleForResponse(p.url, p.page_url);
        if (!mod) continue;
        out[mod.platform].push(...(await mod.parse(p.body, ctx(p.page_url))));
      }
    }
  }
  return out;
}

describe('parser health (fixture replay)', () => {
  it('meets fill-rate thresholds per platform', async () => {
    const byPlatform = await replay();
    const failures: string[] = [];
    const table: Record<string, Record<string, string | number>> = {};
    for (const platform of ['facebook', 'tiktok'] as Platform[]) {
      const posts = byPlatform[platform].filter((r) => r.record_type === 'post');
      const row: Record<string, string | number> = { records: posts.length };
      if (posts.length < thresholds.min_records[platform]) failures.push(`${platform}: ${posts.length} posts < min ${thresholds.min_records[platform]}`);
      for (const [field, min] of Object.entries(thresholds[platform])) {
        const rate = posts.length ? posts.filter((r) => filled(r, field)).length / posts.length : 0;
        row[field] = `${Math.round(rate * 100)}%`;
        if (rate + 1e-9 < min) failures.push(`${platform}.${field}: ${(rate * 100).toFixed(0)}% < ${(min * 100).toFixed(0)}%`);
      }
      table[platform] = row;
    }
    console.table(table);
    expect(failures, failures.join('\n')).toEqual([]);
  });

  it('never throws on garbage or truncated payloads', async () => {
    const garbage = ['', '{', '[]', '{"data":null}', 'for (;;);{"x":1}', String.fromCharCode(0) + '\uFFFD', '{"data":{"node":{"__typename":"Story"}}}'];
    for (const mod of [facebookModule, tiktokModule]) {
      for (const bad of garbage) {
        expect(await mod.parse(bad, ctx('https://www.facebook.com/groups/x'))).toEqual([]);
      }
    }
  });
});
