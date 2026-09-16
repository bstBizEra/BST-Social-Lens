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
import {
  DEFAULT_SETTINGS,
  type CaptureRun,
  type RawPayload,
  type Settings,
  type SocialRecord,
} from '../types';

type SettingsRow = Settings & { id: number };

export class SocialLensDB extends Dexie {
  records!: EntityTable<SocialRecord, 'key'>;
  raw!: EntityTable<RawPayload, 'id'>;
  runs!: EntityTable<CaptureRun, 'id'>;
  settings!: EntityTable<SettingsRow, 'id'>;

  constructor() {
    super('bst-social-lens');
    this.version(1).stores({
      records: 'key, platform, container_id, created_at, captured_at, synced',
      raw: '++id, platform, captured_at',
      runs: '++id, platform, started_at',
      settings: 'id',
    });
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
export async function upsertRecords(records: SocialRecord[]): Promise<number> {
  if (records.length === 0) return 0;
  let written = 0;
  await db.transaction('rw', db.records, async () => {
    for (const rec of records) {
      const existing = await db.records.get(rec.key);
      if (!existing) {
        await db.records.add(rec);
        written++;
        continue;
      }
      // Merge: newer engagement numbers win, keep earliest created_at.
      const merged: SocialRecord = {
        ...existing,
        ...rec,
        created_at: existing.created_at ?? rec.created_at,
        media: rec.media.length ? rec.media : existing.media,
        hashtags: rec.hashtags.length ? rec.hashtags : existing.hashtags,
        synced: 0,
      };
      await db.records.put(merged);
      written++;
    }
  });
  return written;
}

export async function purgeOldRaw(retentionDays: number): Promise<number> {
  const cutoff = new Date(Date.now() - retentionDays * 86_400_000).toISOString();
  return db.raw.where('captured_at').below(cutoff).delete();
}
