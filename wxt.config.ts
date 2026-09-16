import { defineConfig } from 'wxt';

// BST Social Lens — WXT configuration
// Builds: `wxt build` (chrome, default) and `wxt build -b edge`.
export default defineConfig({
  modules: ['@wxt-dev/module-svelte'],
  srcDir: 'src',
  outDir: 'dist',
  manifestVersion: 3,
  manifest: ({ browser }) => ({
    name: 'BST Social Lens',
    short_name: 'Social Lens',
    description:
      'Captures social signals (Facebook groups/posts, TikTok) from your own browser session into a local store and the BST ingest API.',
    version: '0.2.0',
    author: 'BizEra / BST',
    permissions: ['storage', 'unlimitedStorage', 'downloads', 'tabs', 'sidePanel', 'alarms'],
    host_permissions: [
      'https://*.facebook.com/*',
      'https://*.tiktok.com/*',
    ],
    // Allow the ingest API endpoint to be configured; localhost + WSL hosts for dev.
    optional_host_permissions: ['http://localhost/*', 'http://127.0.0.1/*', 'https://*/*'],
    action: { default_title: 'BST Social Lens' },
    side_panel: { default_path: 'sidepanel.html' },
    minimum_chrome_version: '116',
    // Edge and Chrome share the same MV3 package; browser is available for per-target tweaks.
    ...(browser === 'edge' ? {} : {}),
  }),
});
