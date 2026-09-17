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
import { db, getSettings, purgeOldRaw, seenGet, seenMark, setSettings, upsertRecords } from '../lib/db';
import { toCsv, toNdjson } from '../lib/export';
import { matchFields } from '../lib/keywords';
import { moduleForResponse, type ParseContext } from '../lib/modules';
import { normalizeUrl, urlHash } from '../lib/url';
import { contentHashInput, planRawBatch, truncateUtf8 } from '../lib/provenance';
import type { AutoProgress, Platform, RuntimeMessage, SocialRecord, Stats } from '../lib/types';

const SYNC_ALARM = 'bst-social-lens:sync';
const PURGE_ALARM = 'bst-social-lens:purge';

// Ephemeral live status of the autonomous run (source of truth is the content script).
let lastAuto: AutoProgress = { running: false, scrolls: 0, reason: null };

async function relayToActiveTab(message: unknown): Promise<boolean> {
  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return false;
  try {
    await browser.tabs.sendMessage(tab.id, message);
    return true;
  } catch {
    return false; // no content script on this tab (not FB/TikTok)
  }
}

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
    const ctx: ParseContext = {
      page_url: payload.page_url,
      captured_at,
      hashAuthorIds: settings.hashAuthorIds,
      captureComments: settings.captureComments,
      sha256,
    };
    try {
      records = await mod.parse(payload.body, ctx);
      parsed = records.length;
    } catch (e) {
      parse_error = e instanceof Error ? e.message : String(e);
    }
  }

  // Keyword tagging + store-mode gate. A comment match promotes its parent post.
  const set = settings.keywordSet;
  const commentMatchByParent = new Map<string, string[]>();
  for (const r of records) {
    const m = matchFields([r.text, r.author_name], set);
    r.matched_keywords = m.hits;
    r.match_score = m.score;
    if (m.matched) {
      r.matched_via = r.record_type === 'comment' ? 'comment' : (r.text && m.hits.length ? 'post' : 'author');
      if (r.record_type === 'comment' && r.parent_post_id) {
        commentMatchByParent.set(r.parent_post_id, Array.from(new Set([...(commentMatchByParent.get(r.parent_post_id) ?? []), ...m.hits])));
      }
    }
  }
  // Promote posts whose comments matched.
  for (const r of records) {
    if (r.record_type === 'post') {
      const viaComment = commentMatchByParent.get(r.post_id);
      if (viaComment && r.match_score === 0) {
        r.matched_keywords = viaComment;
        r.match_score = viaComment.length;
        r.matched_via = 'comment';
      }
    }
  }

  // Compute url_hash for every record with a permalink (feeds the seen frontier).
  for (const r of records) {
    if (r.permalink) r.url_hash = await urlHash(r.permalink);
  }

  // Store-mode gate: 'matched' keeps only records with a hit (or a promoted post).
  const toStore: SocialRecord[] =
    settings.storeMode === 'all' ? records : records.filter((r) => r.match_score > 0);

  // L0 identity: hash of the body as stored (after truncation) — the server verifies the same hash.
  const { text: storedBody, truncated } = truncateUtf8(payload.body, settings.maxRawBytes);
  const payload_hash = await sha256(storedBody);
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
      body: storedBody,
      parsed_count: parsed,
      parse_error,
      payload_hash,
      truncated,
      synced: 0,
    });
  }

  // Provenance on every record: which payload it came from, and a content hash.
  for (const r of toStore) {
    r.payload_hash = payload_hash;
    r.content_hash = await sha256(contentHashInput(r));
  }
  const stored = await upsertRecords(toStore.map((r) => ({ ...r, raw_ref })));

  // Record permalinks in the seen frontier so they aren't re-opened later.
  for (const r of toStore) {
    if (!r.url_hash || !r.permalink) continue;
    const existing = await seenGet(r.url_hash);
    if (!existing) {
      await seenMark({
        url_hash: r.url_hash,
        url: normalizeUrl(r.permalink),
        platform: r.platform,
        first_seen: captured_at,
        last_status: 'seen',
        fetch_count: 0,
        synced: 0,
      });
    }
  }

  await updateBadge();
  return { stored, parsed, platform, parse_error };
}

async function stats(): Promise<Stats> {
  const [fb, tt, comments, matched, seen, raw, unsynced, settings, last, rawUnsynced] = await Promise.all([
    db.records.where('platform').equals('facebook').count(),
    db.records.where('platform').equals('tiktok').count(),
    db.records.where('record_type').equals('comment').count(),
    db.records.where('match_score').above(0).count(),
    db.seen.count(),
    db.raw.count(),
    db.records.where('synced').equals(0).count(),
    getSettings(),
    db.records.orderBy('captured_at').last(),
    db.raw.where('synced').equals(0).count(),
  ]);
  return {
    records: { facebook: fb, tiktok: tt },
    comments,
    matched,
    seen,
    raw,
    unsynced,
    rawUnsynced,
    captureEnabled: settings.captureEnabled,
    storeMode: settings.storeMode,
    lastCapture: last?.captured_at,
  };
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
  const headers = { 'content-type': 'application/json', ...(settings.ingestToken ? { authorization: `Bearer ${settings.ingestToken}` } : {}) };
  if (batch.length === 0) {
    // Records are clear; raw evidence may still be pending (e.g. sendRaw was just enabled).
    try {
      const raw = settings.sendRaw ? await syncRaw(settings, headers) : { pushedRaw: 0, rawNote: 'sendRaw off' };
      return { pushed: 0, ...raw };
    } catch (e) {
      return { pushed: 0, error: e instanceof Error ? e.message : String(e) };
    }
  }
  try {
    const res = await fetch(settings.ingestUrl, {
      method: 'POST',
      headers,
      body: JSON.stringify({ source: 'bst-social-lens', version: browser.runtime.getManifest().version, records: batch }),
    });
    if (!res.ok) return { pushed: 0, error: `HTTP ${res.status}` };
    await db.records.bulkPut(batch.map((r) => ({ ...r, synced: 1 as const })));
    const raw = settings.sendRaw ? await syncRaw(settings, headers) : { pushedRaw: 0, rawNote: 'sendRaw off' };
    return { pushed: batch.length, ...raw };
  } catch (e) {
    return { pushed: 0, error: e instanceof Error ? e.message : String(e) };
  }
}

const RAW_BATCH_MAX_BYTES = 4_000_000;
const RAW_BATCH_MAX_COUNT = 25;

/** Phase 5: push unsynced raw payloads to POST /raw (same origin as the ingest URL). */
async function syncRaw(settings: Awaited<ReturnType<typeof getSettings>>, headers: Record<string, string>) {
  const pending = await db.raw.where('synced').equals(0).limit(200).toArray();
  if (pending.length === 0) return { pushedRaw: 0, rawNote: 'nothing pending' };
  const plan = planRawBatch(pending.filter((r) => r.payload_hash), RAW_BATCH_MAX_BYTES, RAW_BATCH_MAX_COUNT);
  if (plan.skippedTooLarge.length) await db.raw.bulkPut(plan.skippedTooLarge.map((r) => ({ ...r, synced: 2 as const })));
  if (plan.rows.length === 0) return { pushedRaw: 0, rawSkipped: plan.skippedTooLarge.length, rawNote: 'all pending payloads over the batch cap' };
  const rawUrl = new URL('/raw', settings.ingestUrl).toString();
  const res = await fetch(rawUrl, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      source: 'bst-social-lens',
      version: browser.runtime.getManifest().version,
      captures: plan.rows.map((r) => ({
        payload_hash: r.payload_hash, platform: r.platform === 'unknown' ? null : r.platform, url: r.url, method: r.method, status: r.status,
        source: r.source, page_url: r.page_url, captured_at: r.captured_at, body: r.body, truncated: !!r.truncated,
      })),
    }),
  });
  if (!res.ok) return { pushedRaw: 0, rawError: `HTTP ${res.status}` };
  const result = (await res.json()) as { rejected_hashes?: string[] };
  const rejected = new Set(result.rejected_hashes ?? []);
  await db.raw.bulkPut(plan.rows.map((r) => ({ ...r, synced: (rejected.has(r.payload_hash!) ? 2 : 1) as 1 | 2 })));
  const remaining = Math.max(0, pending.length - plan.rows.length - plan.skippedTooLarge.length);
  return { pushedRaw: plan.rows.length - rejected.size, rawRejected: rejected.size, rawSkipped: plan.skippedTooLarge.length, rawRemaining: remaining };
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
          if (msg.what === 'seen' || msg.what === 'all') await db.seen.clear();
          await updateBadge();
          return { ok: true };
        case 'seenCheck': {
          const hash = await urlHash(msg.url);
          const hit = await seenGet(hash);
          const fresh = hit && (!hit.refresh_after || hit.refresh_after > new Date().toISOString());
          return { seen: !!fresh, status: hit?.last_status ?? null, url_hash: hash };
        }
        case 'seenMark': {
          const hash = await urlHash(msg.url);
          const inserted = await seenMark({
            url_hash: hash,
            url: normalizeUrl(msg.url),
            platform: msg.platform ?? 'unknown',
            first_seen: new Date().toISOString(),
            last_status: msg.status,
            fetch_count: 0,
            synced: 0,
          });
          return { ok: true, inserted, url_hash: hash };
        }
        case 'sync':
          return syncToIngest();
        case 'hostPermission': {
          // The ingest origin is an optional host permission: without it the background fetch is a plain
          // cross-origin request (CORS preflight → "Failed to fetch"). Request must come from a user gesture (side panel click).
          const s = await getSettings();
          let origin: string;
          try {
            origin = new URL(s.ingestUrl).origin + '/*';
          } catch {
            return { granted: false, error: 'invalid ingest url' };
          }
          const has = await browser.permissions.contains({ origins: [origin] });
          if (has || !msg.request) return { granted: has, origin };
          const granted = await browser.permissions.request({ origins: [origin] });
          return { granted, origin };
        }
        case 'autoStart': {
          const s = await getSettings();
          lastAuto = { running: true, scrolls: 0, reason: null };
          const ok = await relayToActiveTab({ type: 'autoStart', config: s.autoRun });
          if (!ok) lastAuto = { running: false, scrolls: 0, reason: 'no-capture-tab' };
          return { ok, running: lastAuto.running };
        }
        case 'autoStop': {
          await relayToActiveTab({ type: 'autoStop' });
          lastAuto = { ...lastAuto, running: false, reason: 'stopped' };
          return { ok: true };
        }
        case 'autoState':
          return lastAuto;
        case 'autoProgress':
          lastAuto = msg.progress;
          return { ok: true };
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
