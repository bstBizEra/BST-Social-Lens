/**
 * Facebook module — parses GraphQL feed responses (`/api/graphql/`) for
 * group feeds, single posts and page feeds.
 *
 * Strategy: Facebook's payload shapes change often, so instead of walking a
 * fixed path we scan every object in the response for the well-known
 * `Story` node signature and pull fields defensively. Anything unparseable
 * yields no record; the raw payload is kept for re-parsing after a fix.
 */
import type { ContainerType, SocialRecord } from '../types';
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

function isComment(o: Obj): boolean {
  return (
    o['__typename'] === 'Comment' &&
    typeof o['id'] === 'string' &&
    (typeof o['legacy_fbid'] === 'string' || typeof o['body'] === 'object' || typeof o['author'] === 'object')
  );
}

function containerType(pageUrl: string, group: Obj | undefined): ContainerType {
  if (group?.['__typename'] === 'Group' || GROUP_RE.test(pageUrl)) return 'group';
  if (/facebook\.com\/(pages|marketplace)\//.test(pageUrl)) return 'page';
  if (/facebook\.com\/groups\/feed\/?/.test(pageUrl)) return 'group';
  if (/facebook\.com\/(profile\.php|[^/]+\/?$)/.test(pageUrl)) return 'profile';
  return 'feed';
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

/**
 * Engagement counts. Live shape (2026-09, group feed):
 *   comet_sections.feedback.story.story_ufi_container.story.feedback_context
 *     .feedback_target_with_context.comet_ufi_summary_and_actions_renderer.feedback
 *     .adaptive_ufi_action_renderers[i].feedback.{reaction_count.count | comment_rendering_instance.comments.total_count | share_count.count}
 * Older/other surfaces put them directly on `feedback`. We walk both subtrees
 * (never `attached_story`, which is the reshared original) and take the first hit.
 */
type Counts = Pick<SocialRecord, 'reactions_total' | 'comments_count' | 'shares_count'>;

function counts(story: Obj): Counts {
  const out: Counts = {};
  const roots: unknown[] = [story['feedback'], (story['comet_sections'] as Obj | undefined)?.['feedback']];
  for (const root of roots) {
    walk(root, (o) => {
      if (out.reactions_total === undefined) {
        out.reactions_total =
          num((o['reaction_count'] as Obj | undefined)?.['count']) ??
          num((o['reactors'] as Obj | undefined)?.['count']) ??
          num((o['unified_reactors'] as Obj | undefined)?.['count']);
      }
      if (out.comments_count === undefined) {
        out.comments_count =
          num((o['comment_count'] as Obj | undefined)?.['total_count']) ??
          num(((o['comment_rendering_instance'] as Obj | undefined)?.['comments'] as Obj | undefined)?.['total_count']) ??
          num(o['total_comment_count']);
      }
      if (out.shares_count === undefined) {
        out.shares_count = num((o['share_count'] as Obj | undefined)?.['count']) ?? num(o['i18n_share_count']);
      }
    });
    if (out.reactions_total !== undefined && out.comments_count !== undefined && out.shares_count !== undefined) break;
  }
  return out;
}

function media(o: Obj): SocialRecord['media'] {
  const out: SocialRecord['media'] = [];
  const contentStory = ((o['comet_sections'] as Obj | undefined)?.['content'] as Obj | undefined)?.['story'] as Obj | undefined;
  const roots = [o['attachments'], contentStory?.['attachments'], contentStory?.['attached_story'], o['attached_story']];
  walk(roots, (m) => {
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

function commentText(o: Obj): string | undefined {
  const body = o['body'] as Obj | undefined;
  return str(body?.['text']) ?? str(o['text']);
}

export const facebookModule: PlatformModule = {
  platform: 'facebook',
  version: '0.3.0',
  matchesPage: (pageUrl) => PAGE_RE.test(pageUrl),
  matches: (url, pageUrl) => PAGE_RE.test(pageUrl) && /\/api\/graphql\/?/.test(url),

  async parse(body: string, ctx: ParseContext): Promise<SocialRecord[]> {
    const docs = parseMultiJson(body);
    if (docs.length === 0) return [];
    const groupMatch = ctx.page_url.match(GROUP_RE);
    const container_id = groupMatch?.[1];
    const byKey = new Map<string, SocialRecord>();
    // Track the most recent post id seen so comments can be attributed even when
    // Facebook nests them under a feedback container without an explicit post ref.
    let lastPostId: string | undefined;

    for (const doc of docs) {
      const stories: Obj[] = [];
      walk(doc, (o) => {
        if (isStory(o)) stories.push(o);
      });
      for (const s of stories) {
        const post_id = str(s['post_id']) ?? str(s['id']);
        if (!post_id) continue;
        lastPostId = post_id;
        const key = `facebook:${post_id}`;
        const actor = firstActor(s);
        const text = messageText(s);
        const owning = (s['feedback'] as Obj | undefined)?.['owning_profile'] as Obj | undefined;
        const author = await authorFields(ctx, 'facebook', str(actor?.['id']) ?? str(owning?.['id']));
        const to = s['to'] as Obj | undefined;
        const group = to?.['__typename'] === 'Group' ? to : ((s['target_group'] as Obj | undefined) ?? undefined);
        const assocGroupId = str(((s['feedback'] as Obj | undefined)?.['associated_group'] as Obj | undefined)?.['id']);
        const rec: SocialRecord = {
          key,
          platform: 'facebook',
          post_id,
          record_type: 'post',
          permalink: str(s['wwwURL']) ?? str(s['url']) ?? str(s['permalink_url']),
          // On the aggregated /groups/feed/ page the URL says nothing; trust the story's own group first.
          container_id: str(group?.['id']) ?? assocGroupId ?? (container_id !== 'feed' ? container_id : undefined),
          container_name: str(group?.['name']),
          container_type: containerType(ctx.page_url, group),
          author_name: str(actor?.['name']) ?? str(owning?.['name']),
          author_url: str(actor?.['url']) ?? str(actor?.['profile_url']),
          ...author,
          text,
          created_at: isoFromUnix(s['creation_time']),
          captured_at: ctx.captured_at,
          ...counts(s),
          media: media(s),
          hashtags: extractHashtags(text),
          parser_version: facebookModule.version,
          matched_keywords: [],
          match_score: 0,
          synced: 0,
        };
        // Facebook repeats the same story in several wrappers; prefer the richest copy.
        const prev = byKey.get(key);
        if (!prev || (rec.text && !prev.text) || (rec.reactions_total !== undefined && prev.reactions_total === undefined)) {
          byKey.set(key, { ...prev, ...rec, media: rec.media.length ? rec.media : prev?.media ?? [] });
        }
      }

      if (ctx.captureComments !== false) {
        const comments: Obj[] = [];
        walk(doc, (o) => {
          if (isComment(o)) comments.push(o);
        });
        for (const c of comments) {
          const cid = str(c['legacy_fbid']) ?? str(c['id']);
          const text = commentText(c);
          if (!cid || !text) continue;
          const cauthor = c['author'] as Obj | undefined;
          const author = await authorFields(ctx, 'facebook', str(cauthor?.['id']));
          const key = `facebook:${cid}`;
          byKey.set(key, {
            key,
            platform: 'facebook',
            post_id: cid,
            record_type: 'comment',
            parent_post_id: lastPostId,
            permalink: str(c['url']),
            container_id: container_id !== 'feed' ? container_id : undefined,
            container_type: containerType(ctx.page_url, undefined),
            author_name: str(cauthor?.['name']),
            author_url: str(cauthor?.['url']),
            ...author,
            text,
            created_at: isoFromUnix(c['created_time']),
            captured_at: ctx.captured_at,
            media: [],
            hashtags: extractHashtags(text),
            parser_version: facebookModule.version,
            matched_keywords: [],
            match_score: 0,
            synced: 0,
          });
        }
      }
    }
    // Drop empty post shells; keep any comment that has text.
    return [...byKey.values()].filter(
      (r) => r.record_type === 'comment' || r.text || r.media.length || r.reactions_total !== undefined,
    );
  },
};
