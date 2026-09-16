/**
 * URL normalization + hashing for the seen-link frontier.
 *
 * Two links to the same post must normalize identically or the frontier leaks
 * (the same permalink gets opened twice). We strip tracking params, lowercase
 * the host, drop the fragment, and canonicalise Facebook's post identifiers.
 */

// Query params that never change identity — strip them everywhere.
const TRACKING_PARAMS = new Set([
  'fbclid', 'mibextid', '__cft__', '__tn__', '__so__', 'rdid', 'paipv',
  'notif_t', 'notif_id', 'ref', 'refid', 'refsrc', 'hc_ref', 'source',
  'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
  '_rdr', 'eav', 'av', 'comment_tracking', 'ft', 'lst',
]);

// Params (prefixes) that are Facebook internal noise.
const TRACKING_PREFIXES = ['__cft__', '__tn__', 'acontext'];

/** Canonicalise a URL to an identity string. Returns input trimmed on failure. */
export function normalizeUrl(input: string): string {
  let u: URL;
  try {
    u = new URL(input);
  } catch {
    return input.trim();
  }
  u.hash = '';
  u.hostname = u.hostname.toLowerCase().replace(/^m\.|^web\./, 'www.');
  if (u.protocol === 'http:') u.protocol = 'https:';

  const keep = new URLSearchParams();
  for (const [k, v] of u.searchParams) {
    const key = k.toLowerCase();
    if (TRACKING_PARAMS.has(key)) continue;
    if (TRACKING_PREFIXES.some((p) => key.startsWith(p))) continue;
    keep.set(k, v);
  }
  // Stable param order so ?a=1&b=2 == ?b=2&a=1.
  const sorted = [...keep.entries()].sort(([a], [b]) => a.localeCompare(b));
  u.search = sorted.length ? '?' + sorted.map(([k, v]) => `${k}=${v}`).join('&') : '';

  // Facebook permalink canonical forms:
  //   /groups/<gid>/posts/<pid>/  and  /groups/<gid>/permalink/<pid>/  → same post
  const m = u.pathname.match(/\/groups\/(\d+)\/(?:posts|permalink)\/(\d+)/);
  if (m) u.pathname = `/groups/${m[1]}/posts/${m[2]}/`;
  // story.php?story_fbid=X&id=Y → keep only those two, ordered.
  if (u.pathname.endsWith('/story.php')) {
    const sfb = keep.get('story_fbid');
    const id = keep.get('id');
    if (sfb && id) u.search = `?id=${id}&story_fbid=${sfb}`;
  }
  // Drop a trailing slash difference by normalising to one.
  u.pathname = u.pathname.replace(/\/+$/, '/') || '/';
  return u.toString();
}

/** SHA-256 of the normalized URL, hex. Uses WebCrypto (available in SW + page). */
export async function urlHash(input: string): Promise<string> {
  const norm = normalizeUrl(input);
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(norm));
  return Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, '0')).join('');
}
