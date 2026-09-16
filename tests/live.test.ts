/**
 * Re-parses raw payloads exported from the extension (tests/fixtures/_live/*.ndjson,
 * git-ignored because they contain real content). Skipped when absent.
 * Prints a field fill-rate table so parser regressions are visible.
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { moduleForResponse } from '../src/lib/modules';
import type { RawPayload, SocialRecord } from '../src/lib/types';

const dir = new URL('./fixtures/_live/', import.meta.url);
const files = existsSync(dir) ? readdirSync(dir).filter((f) => f.endsWith('.ndjson')) : [];

describe.skipIf(files.length === 0)('live raw payload re-parse', () => {
  it('parses every raw payload without throwing and reports fill rates', async () => {
    const all: SocialRecord[] = [];
    for (const f of files) {
      const rows = readFileSync(new URL(f, dir), 'utf8').trim().split('\n').map((l) => JSON.parse(l) as RawPayload);
      for (const p of rows) {
        const mod = moduleForResponse(p.url, p.page_url);
        if (!mod) continue;
        const recs = await mod.parse(p.body, { page_url: p.page_url, captured_at: p.captured_at, hashAuthorIds: true, sha256: async (s) => s });
        all.push(...recs);
      }
    }
    const fields: (keyof SocialRecord)[] = ['permalink', 'container_id', 'container_name', 'author_name', 'author_hash', 'text', 'created_at', 'reactions_total', 'comments_count', 'shares_count', 'media'];
    const table = Object.fromEntries(fields.map((k) => [k, `${Math.round((100 * all.filter((r) => (Array.isArray(r[k]) ? (r[k] as unknown[]).length > 0 : r[k] !== undefined && r[k] !== '')).length) / Math.max(all.length, 1))}%`]));
    console.table({ records: all.length, ...table });
    expect(all.length).toBeGreaterThan(0);
    for (const r of all) {
      expect(r.container_id).not.toBe('feed');
      expect(r.post_id).toMatch(/^\d+$/);
    }
  });
});
