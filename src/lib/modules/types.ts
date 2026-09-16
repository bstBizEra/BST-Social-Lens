import type { Platform, SocialRecord } from '../types';

export interface ParseContext {
  page_url: string;
  captured_at: string;
  hashAuthorIds: boolean;
  /** Async SHA-256 helper injected by the caller (WebCrypto). */
  sha256: (input: string) => Promise<string>;
}

/**
 * One module per platform. `matches` decides whether a captured URL belongs to
 * the module; `parse` turns a raw response body into normalised records.
 * Modules must never throw on unexpected shapes — return [] and let the caller
 * record a parse failure.
 */
export interface PlatformModule {
  platform: Platform;
  /** Bumped whenever the parser's output changes. */
  version: string;
  /** Does this response URL carry data we care about? */
  matches(url: string, pageUrl: string): boolean;
  /** Is the page itself one this module should activate on? */
  matchesPage(pageUrl: string): boolean;
  parse(body: string, ctx: ParseContext): Promise<SocialRecord[]>;
}

/* ---------- Shared helpers ---------- */

/** Facebook GraphQL responses are often several JSON documents separated by newlines. */
export function parseMultiJson(body: string): unknown[] {
  const out: unknown[] = [];
  const trimmed = body.trim();
  if (!trimmed) return out;
  try {
    out.push(JSON.parse(trimmed));
    return out;
  } catch {
    /* fall through to line mode */
  }
  for (const line of trimmed.split('\n')) {
    const l = line.trim();
    if (!l) continue;
    try {
      out.push(JSON.parse(l));
    } catch {
      /* skip non-JSON line */
    }
  }
  return out;
}

/** Depth-first walk over any JSON value, calling `visit` on every object. */
export function walk(value: unknown, visit: (obj: Record<string, unknown>) => void, depth = 0): void {
  if (depth > 60 || value === null || typeof value !== 'object') return;
  if (Array.isArray(value)) {
    for (const v of value) walk(v, visit, depth + 1);
    return;
  }
  const obj = value as Record<string, unknown>;
  visit(obj);
  for (const v of Object.values(obj)) walk(v, visit, depth + 1);
}

export const num = (v: unknown): number | undefined =>
  typeof v === 'number' ? v : typeof v === 'string' && v !== '' && !Number.isNaN(Number(v)) ? Number(v) : undefined;

export const str = (v: unknown): string | undefined => (typeof v === 'string' && v !== '' ? v : undefined);

export const isoFromUnix = (s: unknown): string | undefined => {
  const n = num(s);
  return n ? new Date(n * 1000).toISOString() : undefined;
};

export const extractHashtags = (text: string | undefined): string[] =>
  text ? Array.from(new Set((text.match(/#[\p{L}\p{M}\p{N}_]+/gu) ?? []).map((h) => h.slice(1)))) : [];

export async function authorFields(
  ctx: ParseContext,
  platform: Platform,
  id: string | undefined,
): Promise<{ author_id?: string; author_hash?: string }> {
  if (!id) return {};
  const author_hash = await ctx.sha256(`${platform}:${id}`);
  return ctx.hashAuthorIds ? { author_hash } : { author_id: id, author_hash };
}
