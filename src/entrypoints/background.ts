/**
 * Background service worker.
 *
 * Responsibilities
 *  - receive captured payloads from the bridge, route to a platform module,
 *    persist raw + parsed rows in IndexedDB (Dexie)
 *  - answer stats / export / clear / settings requests from the side panel
 *  - push unsynced records to the BST ingest API (manual or alarm-driven)
 *  - housekeeping: purge old raw payloads
 *
 * MV3 rules honoured here: all listeners registered at top level; no state
 * kept in memory that matters — IndexedDB is the source of truth.
 */
import { db, getSettings, purgeOldRaw, setSettings, upsertRecords } from '../lib/db';
import { toCsv, toNdjson } from '../lib/export';
import { moduleForResponse, type ParseContext } from '../lib/modules';
import type { Platform, RuntimeMessage, Stats } from '../lib/types';

const SYNC_ALARM = 'bst-social-lens:sync';
const PURGE_ALARM = 'bst-social-lens:purge';

async function sha256(input: string): Promise<string> {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(input));
  return Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, '0')).join('');
}

async function handleCapture(payload: Extract<RuntimeMessage, { type: 'capture' }>['payload']) {
  const settings = await getSettings();
  if (!settings.captureEnabled) return { stored: 0, skipped: 'disabled' };

  const mod = moduleForResponse(payload.url, payload.page_url);
  const captured_at = new Date(payload.ts).toISOString();
  const platform: Platform | 'unknown' = mod?.platform ?? 'unknown';

  let parsed = 0;
  let parse_error: string | undefined;
  let records: Awaited<ReturnType<NonNullable<typeof mod>['parse']>> = [];

  if (mod) {
    const ctx: ParseContext = { page_url: payload.page_url, captured_at, hashAuthorIds: settings.hashAuthorIds, sha256 };
    try {
      records = await mod.parse(payload.body, ctx);
      parsed = records.length;
    } catch (e) {
      parse_error = e instanceof Error ? e.message : String(e);
    }
  }

  let raw_ref: number | undefined;
  if (settings.keepRawPayloads) {
    raw_ref = await db.raw.add({
      platform,
      url: payload.url,
      method: payload.method,
      status: payload.status,
      source: payload.kind,
      page_url: payload.page_url,
      captured_at,
      body: payload.body.slice(0, settings.maxRawBytes),
      parsed_count: parsed,
      parse_error,
    });
  }

  const stored = await upsertRecords(records.map((r) => ({ ...r, raw_ref })));
  await updateBadge();
  return { stored, parsed, platform, parse_error };
}

async function stats(): Promise<Stats> {
  const [fb, tt, raw, unsynced, settings, last] = await Promise.all([
    db.records.where('platform').equals('facebook').count(),
    db.records.where('platform').equals('tiktok').count(),
    db.raw.count(),
    db.records.where('synced').equals(0).count(),
    getSettings(),
    db.records.orderBy('captured_at').last(),
  ]);
  return { records: { facebook: fb, tiktok: tt }, raw, unsynced, captureEnabled: settings.captureEnabled, lastCapture: last?.captured_at };
}

async function updateBadge() {
  const total = await db.records.count();
  await browser.action.setBadgeBackgroundColor({ color: '#0f766e' });
  await browser.action.setBadgeText({ text: total > 999 ? `${Math.floor(total / 1000)}k` : total ? String(total) : '' });
}

async function exportRecords(format: 'ndjson' | 'csv', platform?: Platform) {
  const rows = platform ? await db.records.where('platform').equals(platform).toArray() : await db.records.toArray();
  const body = format === 'csv' ? toCsv(rows) : toNdjson(rows);
  const mime = format === 'csv' ? 'text/csv' : 'application/x-ndjson';
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const filename = `bst-social-lens_${platform ?? 'all'}_${stamp}.${format}`;
  // Service workers cannot use URL.createObjectURL → use a data: URL.
  const url = `data:${mime};charset=utf-8,${encodeURIComponent(body)}`;
  await browser.downloads.download({ url, filename, saveAs: false });
  return { count: rows.length, filename };
}

async function exportRaw(platform?: Platform, limit = 200) {
  let q = platform ? db.raw.where('platform').equals(platform) : db.raw.toCollection();
  const rows = await q.reverse().limit(limit).toArray();
  const body = rows.map((r) => JSON.stringify(r)).join('\n') + '\n';
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const filename = `bst-social-lens_raw_${platform ?? 'all'}_${stamp}.ndjson`;
  const url = `data:application/x-ndjson;charset=utf-8,${encodeURIComponent(body)}`;
  await browser.downloads.download({ url, filename, saveAs: false });
  return { count: rows.length, filename };
}

async function syncToIngest() {
  const settings = await getSettings();
  if (!settings.ingestUrl) return { pushed: 0, error: 'no ingest url' };
  const batch = await db.records.where('synced').equals(0).limit(500).toArray();
  if (batch.length === 0) return { pushed: 0 };
  try {
    const res = await fetch(settings.ingestUrl, {
      method: 'POST',
      headers: { 'content-type': 'application/json', ...(settings.ingestToken ? { authorization: `Bearer ${settings.ingestToken}` } : {}) },
      body: JSON.stringify({ source: 'bst-social-lens', version: browser.runtime.getManifest().version, records: batch }),
    });
    if (!res.ok) return { pushed: 0, error: `HTTP ${res.status}` };
    await db.records.bulkPut(batch.map((r) => ({ ...r, synced: 1 as const })));
    return { pushed: batch.length };
  } catch (e) {
    return { pushed: 0, error: e instanceof Error ? e.message : String(e) };
  }
}

export default defineBackground(() => {
  // Clicking the toolbar icon opens the side panel.
  browser.sidePanel?.setPanelBehavior?.({ openPanelOnActionClick: true }).catch(() => {});

  browser.runtime.onInstalled.addListener(async () => {
    await browser.alarms.create(PURGE_ALARM, { periodInMinutes: 60 * 6 });
    await updateBadge();
  });

  browser.alarms.onAlarm.addListener(async (alarm) => {
    if (alarm.name === PURGE_ALARM) {
      const s = await getSettings();
      await purgeOldRaw(s.rawRetentionDays);
    } else if (alarm.name === SYNC_ALARM) {
      await syncToIngest();
    }
  });

  browser.runtime.onMessage.addListener((msg: RuntimeMessage, _sender, sendResponse) => {
    (async () => {
      switch (msg.type) {
        case 'capture':
          return handleCapture(msg.payload);
        case 'stats':
          return stats();
        case 'export':
          return exportRecords(msg.format, msg.platform);
        case 'exportRaw':
          return exportRaw(msg.platform, msg.limit);
        case 'clear':
          if (msg.what === 'records' || msg.what === 'all') await db.records.clear();
          if (msg.what === 'raw' || msg.what === 'all') await db.raw.clear();
          await updateBadge();
          return { ok: true };
        case 'sync':
          return syncToIngest();
        case 'getSettings':
          return getSettings();
        case 'setSettings': {
          const next = await setSettings(msg.settings);
          if (next.autoSync) await browser.alarms.create(SYNC_ALARM, { periodInMinutes: 5 });
          else await browser.alarms.clear(SYNC_ALARM);
          return next;
        }
        default:
          return { error: 'unknown message' };
      }
    })()
      .then(sendResponse)
      .catch((e) => sendResponse({ error: e instanceof Error ? e.message : String(e) }));
    return true; // keep the channel open for the async response
  });
});
