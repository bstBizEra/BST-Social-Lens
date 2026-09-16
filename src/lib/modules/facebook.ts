/**
 * Facebook module — parses GraphQL feed responses (`/api/graphql/`) for
 * group feeds, single posts and page feeds.
 *
 * Strategy: Facebook's payload shapes change often, so instead of walking a
 * fixed path we scan every object in the response for the well-known
 * `Story` node signature and pull fields defensively. Anything unparseable
 * yields no record; the raw payload is kept for re-parsing after a fix.
 */
import type { SocialRecord } from '../types';
import {
  authorFields,
  extractHashtags,
  isoFromUnix,
  num,
  parseMultiJson,
  str,
  walk,
  type ParseContext,
  type PlatformModule,
} from './types';

const PAGE_RE = /^https:\/\/(www|web|m|business)\.facebook\.com\//;
const GROUP_RE = /facebook\.com\/groups\/([^/?#]+)/;

type Obj = Record<string, unknown>;

function isStory(o: Obj): boolean {
  return o['__typename'] === 'Story' && (typeof o['post_id'] === 'string' || typeof o['id'] === 'string');
}

function firstActor(o: Obj): Obj | undefined {
  const actors = o['actors'];
  if (Array.isArray(actors) && actors[0] && typeof actors[0] === 'object') return actors[0] as Obj;
  const owner = o['owning_profile'] ?? o['author'];
  return owner && typeof owner === 'object' ? (owner as Obj) : undefined;
}

function messageText(o: Obj): string | undefined {
  const m = o['message'] as Obj | undefined;
  if (m && typeof m['text'] === 'string') return m['text'];
  const cs = o['comet_sections'] as Obj | undefined;
  const content = cs?.['content'] as Obj | undefined;
  const story = content?.['story'] as Obj | undefined;
  const msg = story?.['message'] as Obj | undefined;
  return str(msg?.['text']);
}

function feedback(o: Obj): Obj | undefined {
  const f = o['feedback'];
  if (f && typeof f === 'object') return f as Obj;
  const cs = o['comet_sections'] as Obj | undefined;
  const fb = (cs?.['feedback'] as Obj | undefined)?.['story'] as Obj | undefined;
  const inner = (fb?.['story_ufi_container'] as Obj | undefined)?.['story'] as Obj | undefined;
  const f2 = inner?.['feedback_context'] as Obj | undefined;
  const f3 = (f2?.['feedback_target_with_context'] as Obj | undefined) ?? (inner?.['feedback'] as Obj | undefined);
  return f3;
}

function counts(f: Obj | undefined): Pick<SocialRecord, 'reactions_total' | 'comments_count' | 'shares_count'> {
  if (!f) return {};
  const reactions_total =
    num((f['reaction_count'] as Obj | undefined)?.['count']) ??
    num((f['reactors'] as Obj | undefined)?.['count']) ??
    num((f['unified_reactors'] as Obj | undefined)?.['count']) ??
    num(f['i18n_reaction_count']);
  const comments_count =
    num((f['comment_count'] as Obj | undefined)?.['total_count']) ??
    num((f['comments_count_summary_renderer'] as Obj | undefined)?.['feedback'] && ((f['comments_count_summary_renderer'] as Obj)['feedback'] as Obj)['total_comment_count']) ??
    num(f['total_comment_count']) ??
    num((f['comment_rendering_instance'] as Obj | undefined)?.['comments'] && (((f['comment_rendering_instance'] as Obj)['comments'] as Obj)['total_count']));
  const shares_count = num((f['share_count'] as Obj | undefined)?.['count']) ?? num(f['i18n_share_count']);
  return { reactions_total, comments_count, shares_count };
}

function media(o: Obj): SocialRecord['media'] {
  const out: SocialRecord['media'] = [];
  walk(o['attachments'] ?? o['attached_story'] ?? [], (m) => {
    const t = m['__typename'];
    if (t === 'Photo' || t === 'GenericAttachmentMedia') {
      const img = (m['image'] ?? m['photo_image'] ?? m['viewer_image']) as Obj | undefined;
      const uri = str(img?.['uri']);
      if (uri) out.push({ kind: 'image', url: uri });
    } else if (t === 'Video') {
      const uri = str(m['playable_url_quality_hd']) ?? str(m['playable_url']) ?? str(m['browser_native_hd_url']);
      const thumb = str((m['preferred_thumbnail'] as Obj | undefined)?.['image'] && (((m['preferred_thumbnail'] as Obj)['image'] as Obj)['uri']));
      if (uri) out.push({ kind: 'video', url: uri, thumbnail: thumb });
    } else if (t === 'ExternalUrl' || t === 'ExternalWebLink') {
      const uri = str(m['url']) ?? str(m['external_url']);
      if (uri) out.push({ kind: 'link', url: uri });
    }
  });
  // dedupe by url
  const seen = new Set<string>();
  return out.filter((m) => (seen.has(m.url) ? false : (seen.add(m.url), true)));
}

export const facebookModule: PlatformModule = {
  platform: 'facebook',
  version: '0.1.0',
  matchesPage: (pageUrl) => PAGE_RE.test(pageUrl),
  matches: (url, pageUrl) => PAGE_RE.test(pageUrl) && /\/api\/graphql\/?/.test(url),

  async parse(body: string, ctx: ParseContext): Promise<SocialRecord[]> {
    const docs = parseMultiJson(body);
    if (docs.length === 0) return [];
    const groupMatch = ctx.page_url.match(GROUP_RE);
    const container_id = groupMatch?.[1];
    const byKey = new Map<string, SocialRecord>();

    for (const doc of docs) {
      const stories: Obj[] = [];
      walk(doc, (o) => {
        if (isStory(o)) stories.push(o);
      });
      for (const s of stories) {
        const post_id = str(s['post_id']) ?? str(s['id']);
        if (!post_id) continue;
        const key = `facebook:${post_id}`;
        const actor = firstActor(s);
        const text = messageText(s);
        const f = feedback(s);
        const author = await authorFields(ctx, 'facebook', str(actor?.['id']));
        const group = (s['to'] as Obj | undefined) ?? (s['target_group'] as Obj | undefined);
        const rec: SocialRecord = {
          key,
          platform: 'facebook',
          post_id,
          permalink: str(s['wwwURL']) ?? str(s['url']) ?? str(s['permalink_url']),
          container_id: container_id ?? str(group?.['id']),
          container_name: str(group?.['name']),
          author_name: str(actor?.['name']),
          author_url: str(actor?.['url']) ?? str(actor?.['profile_url']),
          ...author,
          text,
          created_at: isoFromUnix(s['creation_time']),
          captured_at: ctx.captured_at,
          ...counts(f),
          media: media(s),
          hashtags: extractHashtags(text),
          parser_version: facebookModule.version,
          synced: 0,
        };
        // Facebook repeats the same story in several wrappers; prefer the richest copy.
        const prev = byKey.get(key);
        if (!prev || (rec.text && !prev.text) || (rec.reactions_total !== undefined && prev.reactions_total === undefined)) {
          byKey.set(key, { ...prev, ...rec, media: rec.media.length ? rec.media : prev?.media ?? [] });
        }
      }
    }
    // Drop empty shells (no text, no media, no counts) — usually placeholders.
    return [...byKey.values()].filter((r) => r.text || r.media.length || r.reactions_total !== undefined);
  },
};
