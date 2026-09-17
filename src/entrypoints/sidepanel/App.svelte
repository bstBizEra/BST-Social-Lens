<script lang="ts">
  import { onMount } from 'svelte';
  import type { Platform, RuntimeMessage, Settings, Stats } from '../../lib/types';

  let stats = $state<Stats | undefined>(undefined);
  let settings = $state<Settings | undefined>(undefined);
  let message = $state('');
  let busy = $state(false);
  let auto = $state<{ running: boolean; scrolls: number; clicks: number; reason: string | null }>({ running: false, scrolls: 0, clicks: 0, reason: null });

  const autoReason: Record<string, string> = {
    maxScrolls: 'reached scroll cap',
    maxMinutes: 'reached time cap',
    endOfFeed: 'reached end of feed',
    stopped: 'stopped',
    'no-capture-tab': 'open a Facebook/TikTok tab first',
    navigated: 'stopped — page changed',
  };

  async function refreshAuto() {
    auto = await send<{ running: boolean; scrolls: number; clicks: number; reason: string | null }>({ type: 'autoState' });
  }
  const startAuto = () => run('Start', () => send<{ ok: boolean; running: boolean }>({ type: 'autoStart' }), (r) => (r.running ? 'Auto-scroll started' : 'Open a Facebook or TikTok tab first'));
  const stopAuto = () => run('Stop', () => send<{ ok: boolean }>({ type: 'autoStop' }), () => 'Auto-scroll stopped');

  const send = <T,>(msg: RuntimeMessage) => browser.runtime.sendMessage(msg) as Promise<T>;

  async function refresh() {
    stats = await send<Stats>({ type: 'stats' });
  }

  async function loadSettings() {
    settings = await send<Settings>({ type: 'getSettings' });
  }

  async function patch(p: Partial<Settings>) {
    settings = await send<Settings>({ type: 'setSettings', settings: p });
    await refresh();
  }

  async function run<T>(label: string, fn: () => Promise<T>, fmt: (r: T) => string) {
    busy = true;
    message = `${label}…`;
    try {
      const r = await fn();
      message = fmt(r);
    } catch (e) {
      message = `${label} failed: ${e instanceof Error ? e.message : String(e)}`;
    } finally {
      busy = false;
      await refresh();
    }
  }

  const exportAs = (format: 'ndjson' | 'csv', platform?: Platform) =>
    run(`Export ${format}`, () => send<{ count: number; filename: string }>({ type: 'export', format, platform }), (r) => `Saved ${r.count} records → ${r.filename}`);

  const exportRaw = (platform?: Platform) =>
    run('Export raw', () => send<{ count: number; filename: string }>({ type: 'exportRaw', platform }), (r) => `Saved ${r.count} raw payloads → ${r.filename}`);

  type SyncResult = { pushed: number; error?: string; serverTotal?: number; pushedRaw?: number; rawRejected?: number; rawSkipped?: number; rawRemaining?: number; rawError?: string; rawNote?: string };
  function fmtSync(r: SyncResult) {
    if (r.error) return `Sync error: ${r.error}`;
    const raw = r.rawError
      ? `raw: error ${r.rawError}`
      : r.rawNote
        ? `raw: ${r.rawNote}`
        : `raw: ${r.pushedRaw ?? 0} pushed${r.rawRejected ? `, ${r.rawRejected} rejected` : ''}${r.rawSkipped ? `, ${r.rawSkipped} too large` : ''}${r.rawRemaining ? `, ${r.rawRemaining} pending` : ''}`;
    const head = r.pushed === 0 && r.serverTotal !== undefined ? `Connected — nothing to push (server holds ${r.serverTotal} records)` : `Pushed ${r.pushed} records`;
    return `${head} · ${raw}`;
  }
  const sync = () => run('Sync', () => send<SyncResult>({ type: 'sync' }), fmtSync);

  // Host permission for the ingest origin (optional_host_permissions): checked on load, requested on click.
  let hostGranted = $state<boolean | undefined>(undefined);
  async function checkHost() {
    try {
      hostGranted = (await send<{ granted: boolean }>({ type: 'hostPermission', request: false })).granted;
    } catch {
      hostGranted = undefined;
    }
  }
  const grantHost = () =>
    run('Grant access', () => send<{ granted: boolean; origin?: string; error?: string }>({ type: 'hostPermission', request: true }), (r) => {
      hostGranted = r.granted;
      return r.granted ? `Access granted for ${r.origin}` : (r.error ?? 'Access not granted');
    });

  const clear = (what: 'records' | 'raw' | 'seen' | 'all') => {
    if (!confirm(`Clear ${what}? This cannot be undone.`)) return;
    return run(`Clear ${what}`, () => send<{ ok: boolean }>({ type: 'clear', what }), () => `Cleared ${what}`);
  };

  // Keyword set edited as a comma/space/newline-separated string.
  let includeText = $state('');
  let excludeText = $state('');
  $effect(() => {
    if (settings) {
      includeText = settings.keywordSet.include.join(', ');
      excludeText = (settings.keywordSet.exclude ?? []).join(', ');
    }
  });
  const splitTerms = (s: string) => s.split(/[,\n]/).map((t) => t.trim()).filter(Boolean);
  function saveKeywords() {
    if (!settings) return;
    patch({
      keywordSet: {
        ...settings.keywordSet,
        include: splitTerms(includeText),
        exclude: splitTerms(excludeText),
      },
    });
  }

  onMount(() => {
    refresh();
    loadSettings().then(checkHost);
    refreshAuto();
    const t = setInterval(refresh, 3000);
    const a = setInterval(refreshAuto, 1500);
    return () => {
      clearInterval(t);
      clearInterval(a);
    };
  });
</script>

<h1><span class="dot" class:off={!stats?.captureEnabled}></span> BST Social Lens</h1>

<section class="card">
  <h2>Capture</h2>
  <div class="stats">
    <div class="stat"><b>{stats?.records.facebook ?? 0}</b><span>Facebook</span></div>
    <div class="stat"><b>{stats?.records.tiktok ?? 0}</b><span>TikTok</span></div>
    <div class="stat"><b>{stats?.matched ?? 0}</b><span>Matched</span></div>
  </div>
  <div class="stats">
    <div class="stat"><b>{stats?.comments ?? 0}</b><span>Comments</span></div>
    <div class="stat"><b>{stats?.seen ?? 0}</b><span>Seen links</span></div>
    <div class="stat"><b>{stats?.raw ?? 0}</b><span>Raw</span></div>
  </div>
  <div class="muted">
    {#if stats?.lastCapture}Last capture {new Date(stats.lastCapture).toLocaleString()}{:else}No captures yet — open a Facebook group or TikTok page and scroll.{/if}
    {#if stats?.unsynced} · {stats.unsynced} unsynced{/if}{#if stats?.rawUnsynced} · {stats.rawUnsynced} raw unsynced{/if}
    {#if stats} · store mode: {stats.storeMode}{/if}
  </div>
  {#if settings}
    <label class="toggle">Capture enabled <input type="checkbox" checked={settings.captureEnabled} onchange={(e) => patch({ captureEnabled: e.currentTarget.checked })} /></label>
  {/if}
</section>

<section class="card">
  <h2>Autonomous mode</h2>
  <div class="muted">
    {#if auto.running}
      <span class="dot"></span> Running — {auto.scrolls} scrolls{#if settings?.assist.enabled} · {auto.clicks} threads expanded{/if}. Keep this tab and panel open.
    {:else}
      Manual by default (scroll to capture). Start auto-scroll on the current Facebook/TikTok tab.
      {#if auto.reason && autoReason[auto.reason]}· last run: {autoReason[auto.reason]}{/if}
    {/if}
  </div>
  <div class="row">
    {#if auto.running}
      <button class="danger" onclick={stopAuto}>Stop</button>
    {:else}
      <button class="primary" disabled={busy} onclick={startAuto}>Start auto-scroll</button>
    {/if}
  </div>
  {#if settings}
    <div class="row">
      <label>Max scrolls
        <input type="number" min="1" max="500" value={settings.autoRun.maxScrolls} onchange={(e) => settings && patch({ autoRun: { ...settings.autoRun, maxScrolls: Number(e.currentTarget.value) || 40 } })} />
      </label>
      <label>Max minutes
        <input type="number" min="1" max="120" value={settings.autoRun.maxMinutes} onchange={(e) => settings && patch({ autoRun: { ...settings.autoRun, maxMinutes: Number(e.currentTarget.value) || 10 } })} />
      </label>
    </div>
    <div class="muted">Human-like pacing ({(settings.autoRun.minDelayMs / 1000).toFixed(1)}–{(settings.autoRun.maxDelayMs / 1000).toFixed(1)}s between scrolls). Auto-stops at a cap or end of feed. Use a secondary account.</div>
    <label class="toggle">Expand comment threads (assisted)
      <input type="checkbox" checked={settings.assist.enabled} onchange={(e) => settings && patch({ assist: { ...settings.assist, enabled: e.currentTarget.checked } })} />
    </label>
    {#if settings.assist.enabled}
      <div class="row">
        <label>Max expands / run
          <input type="number" min="1" max="200" value={settings.assist.maxClicks} onchange={(e) => settings && patch({ assist: { ...settings.assist, maxClicks: Number(e.currentTarget.value) || 30 } })} />
        </label>
        <label>Per scroll
          <input type="number" min="1" max="10" value={settings.assist.clicksPerRound} onchange={(e) => settings && patch({ assist: { ...settings.assist, clicksPerRound: Number(e.currentTarget.value) || 3 } })} />
        </label>
      </div>
      <label class="toggle">Also expand "See more" text
        <input type="checkbox" checked={settings.assist.expandText} onchange={(e) => settings && patch({ assist: { ...settings.assist, expandText: e.currentTarget.checked } })} />
      </label>
      <div class="muted">Clicks only in-page "View more comments" / "View replies" / "See more" controls (English, Lao, Thai) with {(settings.assist.minDelayMs / 1000).toFixed(1)}–{(settings.assist.maxDelayMs / 1000).toFixed(1)}s pacing. Never opens links or leaves the page; stops if the page changes.</div>
    {/if}
  {/if}
</section>

<section class="card">
  <h2>Keywords &amp; filtering</h2>
  {#if settings}
    <label>Include terms (comma or newline separated)
      <input type="text" value={includeText} oninput={(e) => (includeText = e.currentTarget.value)} onblur={saveKeywords} placeholder="ດິນ, ຂາຍ, ເຊົ່າ, ລາຄາ, ເນື້ອທີ່, ບ້ານ, ເມືອງ, ແຂວງ, location, google map, lat, long" />
    </label>
    <label>Exclude terms
      <input type="text" value={excludeText} oninput={(e) => (excludeText = e.currentTarget.value)} onblur={saveKeywords} placeholder="(optional)" />
    </label>
    <label>Min. distinct matches
      <input type="number" min="1" max="10" value={settings.keywordSet.min_hits ?? 1} onchange={(e) => settings && patch({ keywordSet: { ...settings.keywordSet, min_hits: Number(e.currentTarget.value) || 1 } })} />
    </label>
    <label class="toggle">Store mode: matched only
      <input type="checkbox" checked={settings.storeMode === 'matched'} onchange={(e) => patch({ storeMode: e.currentTarget.checked ? 'matched' : 'all' })} />
    </label>
    <label class="toggle">Capture comments <input type="checkbox" checked={settings.captureComments} onchange={(e) => patch({ captureComments: e.currentTarget.checked })} /></label>
    <div class="muted">Matched-only keeps just keyword hits (posts + comments). A matching comment also keeps its parent post. Lao matching is substring-based (NFC-normalized), so ຂາຍດິນ matches both ຂາຍ and ດິນ.</div>
  {/if}
</section>

<section class="card">
  <h2>Export</h2>
  <div class="row">
    <button class="primary" disabled={busy} onclick={() => exportAs('csv')}>CSV (all)</button>
    <button disabled={busy} onclick={() => exportAs('ndjson')}>NDJSON (all)</button>
    <button disabled={busy} onclick={() => exportAs('csv', 'facebook')}>CSV · Facebook</button>
    <button disabled={busy} onclick={() => exportAs('csv', 'tiktok')}>CSV · TikTok</button>
  </div>
  <div class="row">
    <button disabled={busy} onclick={() => exportRaw()}>Raw payloads (NDJSON, last 200)</button>
  </div>
  <div class="muted">Raw export is for building parser fixtures when records stay at 0.</div>
</section>

<section class="card">
  <h2>BST Ingest API</h2>
  {#if settings}
    <label>Ingest URL <input type="url" value={settings.ingestUrl} onchange={(e) => { patch({ ingestUrl: e.currentTarget.value }).then(checkHost); }} placeholder="http://localhost:7710/ingest" /></label>
    {#if hostGranted === false}
      <div class="row"><span class="muted">The browser has not granted this extension access to the ingest server yet — sync fails with "Failed to fetch" until it does.</span> <button disabled={busy} onclick={grantHost}>Grant access</button></div>
    {/if}
    <label>Bearer token <input type="password" value={settings.ingestToken} onchange={(e) => patch({ ingestToken: e.currentTarget.value })} /></label>
    <label class="toggle">Auto-sync every 5 min <input type="checkbox" checked={settings.autoSync} onchange={(e) => patch({ autoSync: e.currentTarget.checked })} /></label>
    <label class="toggle">Send raw evidence (L0) with sync <input type="checkbox" checked={settings.sendRaw} onchange={(e) => patch({ sendRaw: e.currentTarget.checked })} /></label>
    <div class="muted">Raw payloads let the server trace every record back to the exact response it was parsed from (provenance). Bodies are retained on the server for a limited time; hashes are kept.</div>
    <div class="row"><button class="primary" disabled={busy} onclick={sync}>Sync now</button></div>
  {/if}
</section>

<section class="card">
  <h2>Privacy & storage</h2>
  {#if settings}
    <label class="toggle">Hash author IDs (store SHA-256 only) <input type="checkbox" checked={settings.hashAuthorIds} onchange={(e) => patch({ hashAuthorIds: e.currentTarget.checked })} /></label>
    <label class="toggle">Keep raw payloads (for re-parsing) <input type="checkbox" checked={settings.keepRawPayloads} onchange={(e) => patch({ keepRawPayloads: e.currentTarget.checked })} /></label>
    <label>Raw retention (days) <input type="number" min="1" max="365" value={settings.rawRetentionDays} onchange={(e) => patch({ rawRetentionDays: Number(e.currentTarget.value) || 30 })} /></label>
  {/if}
  <div class="row">
    <button class="danger" disabled={busy} onclick={() => clear('raw')}>Clear raw</button>
    <button class="danger" disabled={busy} onclick={() => clear('seen')}>Clear seen links</button>
    <button class="danger" disabled={busy} onclick={() => clear('all')}>Clear everything</button>
  </div>
</section>

{#if message}<div class="msg">{message}</div>{/if}
