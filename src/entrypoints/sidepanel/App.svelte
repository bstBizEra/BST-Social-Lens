<script lang="ts">
  import { onMount } from 'svelte';
  import type { Platform, RuntimeMessage, Settings, Stats } from '../../lib/types';

  let stats = $state<Stats | undefined>(undefined);
  let settings = $state<Settings | undefined>(undefined);
  let message = $state('');
  let busy = $state(false);

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

  const sync = () => run('Sync', () => send<{ pushed: number; error?: string }>({ type: 'sync' }), (r) => (r.error ? `Sync error: ${r.error}` : `Pushed ${r.pushed} records`));

  const clear = (what: 'records' | 'raw' | 'all') => {
    if (!confirm(`Clear ${what}? This cannot be undone.`)) return;
    return run(`Clear ${what}`, () => send<{ ok: boolean }>({ type: 'clear', what }), () => `Cleared ${what}`);
  };

  onMount(() => {
    refresh();
    loadSettings();
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  });
</script>

<h1><span class="dot" class:off={!stats?.captureEnabled}></span> BST Social Lens</h1>

<section class="card">
  <h2>Capture</h2>
  <div class="stats">
    <div class="stat"><b>{stats?.records.facebook ?? 0}</b><span>Facebook</span></div>
    <div class="stat"><b>{stats?.records.tiktok ?? 0}</b><span>TikTok</span></div>
    <div class="stat"><b>{stats?.raw ?? 0}</b><span>Raw payloads</span></div>
  </div>
  <div class="muted">
    {#if stats?.lastCapture}Last capture {new Date(stats.lastCapture).toLocaleString()}{:else}No captures yet — open a Facebook group or TikTok page and scroll.{/if}
    {#if stats?.unsynced} · {stats.unsynced} unsynced{/if}
  </div>
  {#if settings}
    <label class="toggle">Capture enabled <input type="checkbox" checked={settings.captureEnabled} onchange={(e) => patch({ captureEnabled: e.currentTarget.checked })} /></label>
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
    <label>Ingest URL <input type="url" value={settings.ingestUrl} onchange={(e) => patch({ ingestUrl: e.currentTarget.value })} placeholder="http://localhost:7710/ingest" /></label>
    <label>Bearer token <input type="password" value={settings.ingestToken} onchange={(e) => patch({ ingestToken: e.currentTarget.value })} /></label>
    <label class="toggle">Auto-sync every 5 min <input type="checkbox" checked={settings.autoSync} onchange={(e) => patch({ autoSync: e.currentTarget.checked })} /></label>
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
    <button class="danger" disabled={busy} onclick={() => clear('all')}>Clear everything</button>
  </div>
</section>

{#if message}<div class="msg">{message}</div>{/if}
