/**
 * Provenance helpers (Phase 5 / SLL-PROP-DATA-001B) — pure, testable.
 *
 * - `contentHash()`   : stable hash input for a SocialRecord's *content* (not engagement),
 *                       so re-captures with unchanged content share a hash.
 * - `planRawBatch()`  : pick which raw payload rows to send in one POST /raw call,
 *                       bounded by count and total bytes (service workers are memory-shy).
 */
import type { RawPayload, SocialRecord } from './types';

/** Canonical string for a record's content fields — hashed by the caller with SHA-256. */
export function contentHashInput(r: Pick<SocialRecord, 'platform' | 'post_id' | 'record_type' | 'text' | 'created_at' | 'permalink' | 'media' | 'hashtags' | 'author_hash'>): string {
  return [
    r.platform,
    r.post_id,
    r.record_type,
    (r.text ?? '').normalize('NFC'),
    r.created_at ?? '',
    r.permalink ?? '',
    (r.media ?? []).map((m) => m.url).join(','),
    (r.hashtags ?? []).join(','),
    r.author_hash ?? '',
  ].join('');
}

export interface RawBatchPlan<T extends Pick<RawPayload, 'id' | 'body'>> {
  rows: T[];
  bytes: number;
  skippedTooLarge: T[];
}

/**
 * Take rows in order until `maxCount` or `maxBytes` would be exceeded. A single row larger
 * than `maxBytes` is reported in `skippedTooLarge` (caller marks it so it is not retried forever).
 */
export function planRawBatch<T extends Pick<RawPayload, 'id' | 'body'>>(rows: T[], maxBytes: number, maxCount: number): RawBatchPlan<T> {
  const out: T[] = [];
  const skippedTooLarge: T[] = [];
  let bytes = 0;
  for (const r of rows) {
    const b = utf8Length(r.body);
    if (b > maxBytes) {
      skippedTooLarge.push(r);
      continue;
    }
    if (out.length >= maxCount || bytes + b > maxBytes) break;
    out.push(r);
    bytes += b;
  }
  return { rows: out, bytes, skippedTooLarge };
}

/** UTF-8 byte length without allocating a Buffer (works in SW and Node). */
export function utf8Length(s: string): number {
  let n = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c < 0x80) n += 1;
    else if (c < 0x800) n += 2;
    else if (c >= 0xd800 && c <= 0xdbff) { n += 4; i++; } // surrogate pair
    else n += 3;
  }
  return n;
}
