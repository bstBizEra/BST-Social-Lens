/**
 * Dexie (IndexedDB) store — usable from the service worker and extension pages.
 *
 * Tables
 *  - records:  normalised SocialRecord, keyed by `${platform}:${post_id}`
 *  - raw:      RawPayload rows (retained `rawRetentionDays`)
 *  - runs:     capture sessions per page
 *  - settings: single row (id = 1)
 */
import Dexie, { type EntityTable } from 'dexie';
import { decideSighting } from '../provenance';
import {
  DEFAULT_SETTINGS,
  type CaptureRun,
  type RawPayload,
  type SeenLink,
  type Settings,
  type SocialRecord,
} from '../types';

type SettingsRow = Settings & { id: number };

export class SocialLensDB extends Dexie {
  records!: EntityTable<SocialRecord, 'key'>;
  raw!: EntityTable<RawPayload, 'id'>;
  runs!: EntityTable<CaptureRun, 'id'>;
  settings!: EntityTable<SettingsRow, 'id'>;
  seen!: EntityTable<SeenLink, 'url_hash'>;

  constructor() {
    super('bst-social-lens');
    this.version(1).stores({
      records: 'key, platform, container_id, created_at, captured_at, synced',
      raw: '++id, platform, captured_at',
      runs: '++id, platform, started_at',
      settings: 'id',
    });
    // v2: keyword frontier — record_type/parent index + seen-link table.
    this.version(2).stores({
      records: 'key, platform, record_type, container_id, parent_post_id, created_at, captured_at, synced, match_score',
      raw: '++id, platform, captured_at',
      runs: '++id, platform, started_at',
      settings: 'id',
      seen: 'url_hash, platform, last_status, synced',
    });
    // v3 (Phase 5): raw payloads carry payload_hash + synced so they can be pushed to POST /raw.
    this.version(3).stores({
      records: 'key, platform, record_type, container_id, parent_post_id, created_at, captured_at, synced, match_score',
      raw: '++id, platform, captured_at, synced, payload_hash',
      runs: '++id, platform, started_at',
      settings: 'id',
      seen: 'url_hash, platform, last_status, synced',
    }).upgrade((tx) => tx.table('raw').toCollection().modify((r: { synced?: number }) => { if (r.synced === undefined) r.synced = 0; }));
    // v4 (0.7.2): sighting contexts — existing rows get their container as the one known context.
    this.version(4).stores({
      records: 'key, platform, record_type, container_id, parent_post_id, created_at, captured_at, synced, match_score',
      raw: '++id, platform, captured_at, synced, payload_hash',
      runs: '++id, platform, started_at',
      settings: 'id',
      seen: 'url_hash, platform, last_status, synced',
    }).upgrade((tx) => tx.table('records').toCollection().modify((r: { contexts?: string[]; sightings?: number; container_id?: string }) => {
      if (!r.contexts) r.contexts = r.container_id ? [`container:${r.container_id}`] : [];
      if (!r.sightings) r.sightings = 1;
    }));
  }
}

export const db = new SocialLensDB();

export async function getSettings(): Promise<Settings> {
  const row = await db.settings.get(1);
  return { ...DEFAULT_SETTINGS, ...(row ?? {}) };
}

export async function setSettings(patch: Partial<Settings>): Promise<Settings> {
  const current = await getSettings();
  const next = { ...current, ...patch };
  await db.settings.put({ ...next, id: 1 });
  return next;
}

/** Upsert records; existing rows keep their `synced` flag unless content changed. */
export interface UpsertResult {
  written: number;
  repeats: number;
  newContexts: number;
  changed: number;
}

/**
 * Store records with sighting semantics (0.7.2): a record already stored is touched only when it is seen from a
 * NEW context or its content changed; a same-context, same-content re-sighting is a repeat and is skipped (not
 * re-queued for sync). `ctx` is the sighting context of this capture (see `sightingContext`).
 */
export async function upsertRecords(records: SocialRecord[], ctx = 'page:unknown'): Promise<UpsertResult> {
  const res: UpsertResult = { written: 0, repeats: 0, newContexts: 0, changed: 0 };
  if (records.length === 0) return res;
  await db.transaction('rw', db.records, async () => {
    for (const rec of records) {
      const existing = await db.records.get(rec.key);
      const decision = decideSighting(existing, rec, ctx);
      if (decision === 'repeat') {
        res.repeats++;
        continue;
      }
      if (!existing) {
        await db.records.add({ ...rec, contexts: [ctx], sightings: 1, sighting_context: ctx });
        res.written++;
        continue;
      }
      // Merge: newer engagement numbers win, keep earliest created_at, union keywords and contexts.
      const merged: SocialRecord = {
        ...existing,
        ...rec,
        created_at: existing.created_at ?? rec.created_at,
        media: rec.media.length ? rec.media : existing.media,
        hashtags: rec.hashtags.length ? rec.hashtags : existing.hashtags,
        matched_keywords: Array.from(new Set([...(existing.matched_keywords ?? []), ...(rec.matched_keywords ?? [])])),
        match_score: Math.max(existing.match_score ?? 0, rec.match_score ?? 0),
        contexts: Array.from(new Set([...(existing.contexts ?? []), ctx])),
        sightings: (existing.sightings ?? 1) + 1,
        sighting_context: ctx,
        synced: 0,
      };
      await db.records.put(merged);
      res.written++;
      if (decision === 'new_context') res.newContexts++;
      else res.changed++;
    }
  });
  return res;
}

/** Returns the SeenLink if the normalized url_hash is already known. */
export async function seenGet(url_hash: string): Promise<SeenLink | undefined> {
  return db.seen.get(url_hash);
}

/** Insert or update a frontier entry. Returns true if it was newly inserted. */
export async function seenMark(link: SeenLink): Promise<boolean> {
  const existing = await db.seen.get(link.url_hash);
  if (!existing) {
    await db.seen.add(link);
    return true;
  }
  await db.seen.put({
    ...existing,
    last_status: link.last_status,
    fetch_count: existing.fetch_count + (link.last_status === 'fetched' ? 1 : 0),
    refresh_after: link.refresh_after ?? existing.refresh_after,
    synced: 0,
  });
  return false;
}

export async function purgeOldRaw(retentionDays: number): Promise<number> {
  const cutoff = new Date(Date.now() - retentionDays * 86_400_000).toISOString();
  return db.raw.where('captured_at').below(cutoff).delete();
}
