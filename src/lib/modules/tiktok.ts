/**
 * TikTok module — parses web API responses and the embedded rehydration
 * state on tiktok.com.
 *
 * Endpoints seen in the wild (all return JSON with `itemList` / `item_list`):
 *   /api/post/item_list/        profile videos
 *   /api/challenge/item_list/   hashtag videos
 *   /api/search/item/full/      search results
 *   /api/recommend/item_list/   For You feed
 *   /api/related/item_list/     related videos
 * Comments: /api/comment/list/  → `comments[]` (stored as records of kind comment in v0.2)
 * Embedded: <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"> → webapp.video-detail.itemInfo.itemStruct
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

const PAGE_RE = /^https:\/\/(www\.)?tiktok\.com\//;
const API_RE = /\/api\/(post|challenge|recommend|related|user)\/item_list\/|\/api\/search\/(item|general)\/full\/|\/api\/comment\/list\//;
const TAG_RE = /tiktok\.com\/tag\/([^/?#]+)/;
const SEARCH_RE = /tiktok\.com\/search(?:\/video)?\?q=([^&]+)/;

type Obj = Record<string, unknown>;

function isItem(o: Obj): boolean {
  return typeof o['id'] === 'string' && typeof o['desc'] === 'string' && typeof o['author'] === 'object' && o['author'] !== null && typeof o['stats'] === 'object';
}

function containerFromPage(pageUrl: string): { container_id?: string; container_name?: string; container_type: ContainerType } {
  const tag = pageUrl.match(TAG_RE)?.[1];
  if (tag) return { container_id: `tag:${decodeURIComponent(tag)}`, container_name: `#${decodeURIComponent(tag)}`, container_type: 'hashtag' };
  const q = pageUrl.match(SEARCH_RE)?.[1];
  if (q) return { container_id: `search:${decodeURIComponent(q)}`, container_name: `search "${decodeURIComponent(q)}"`, container_type: 'search' };
  const user = pageUrl.match(/tiktok\.com\/@([^/?#]+)/)?.[1];
  if (user) return { container_id: `user:${user}`, container_name: `@${user}`, container_type: 'profile' };
  return { container_type: 'feed' };
}

/** A TikTok comment node from /api/comment/list/. */
function isTikTokComment(o: Obj): boolean {
  return typeof o['cid'] === 'string' && typeof o['text'] === 'string' && typeof o['user'] === 'object' && o['user'] !== null;
}

export const tiktokModule: PlatformModule = {
  platform: 'tiktok',
  version: '0.3.0',
  matchesPage: (pageUrl) => PAGE_RE.test(pageUrl),
  matches: (url, pageUrl) => PAGE_RE.test(pageUrl) && (API_RE.test(url) || url === 'embedded:__UNIVERSAL_DATA_FOR_REHYDRATION__'),

  async parse(body: string, ctx: ParseContext): Promise<SocialRecord[]> {
    const docs = parseMultiJson(body);
    if (docs.length === 0) return [];
    const container = containerFromPage(ctx.page_url);
    const byKey = new Map<string, SocialRecord>();

    for (const doc of docs) {
      const items: Obj[] = [];
      walk(doc, (o) => {
        if (isItem(o)) items.push(o);
      });
      for (const it of items) {
        const post_id = str(it['id']);
        if (!post_id) continue;
        const author = it['author'] as Obj;
        const stats = it['stats'] as Obj;
        const uniqueId = str(author['uniqueId']);
        const video = it['video'] as Obj | undefined;
        const text = str(it['desc']);
        const tags = Array.isArray(it['textExtra'])
          ? (it['textExtra'] as Obj[]).map((t) => str(t['hashtagName'])).filter((h): h is string => !!h)
          : [];
        const a = await authorFields(ctx, 'tiktok', str(author['id']));
        const media: SocialRecord['media'] = [];
        const play = str(video?.['playAddr']) ?? str(video?.['downloadAddr']);
        if (play) media.push({ kind: 'video', url: play, thumbnail: str(video?.['cover']) });
        const rec: SocialRecord = {
          key: `tiktok:${post_id}`,
          platform: 'tiktok',
          post_id,
          record_type: 'post',
          permalink: uniqueId ? `https://www.tiktok.com/@${uniqueId}/video/${post_id}` : undefined,
          ...container,
          author_name: str(author['nickname']) ?? uniqueId,
          author_url: uniqueId ? `https://www.tiktok.com/@${uniqueId}` : undefined,
          ...a,
          text,
          lang: str(it['textLanguage']),
          created_at: isoFromUnix(it['createTime']),
          captured_at: ctx.captured_at,
          reactions_total: num(stats['diggCount']),
          comments_count: num(stats['commentCount']),
          shares_count: num(stats['shareCount']),
          views_count: num(stats['playCount']),
          media,
          hashtags: tags.length ? Array.from(new Set(tags)) : extractHashtags(text),
          parser_version: tiktokModule.version,
          matched_keywords: [],
          match_score: 0,
          synced: 0,
        };
        byKey.set(rec.key, rec);
      }

      if (ctx.captureComments !== false) {
        const comments: Obj[] = [];
        walk(doc, (o) => {
          if (isTikTokComment(o)) comments.push(o);
        });
        for (const c of comments) {
          const cid = str(c['cid']);
          const text = str(c['text']);
          if (!cid || !text) continue;
          const user = c['user'] as Obj | undefined;
          const uniqueId = str(user?.['unique_id']) ?? str(user?.['uniqueId']);
          const a = await authorFields(ctx, 'tiktok', str(user?.['uid']) ?? str(user?.['id']));
          byKey.set(`tiktok:${cid}`, {
            key: `tiktok:${cid}`,
            platform: 'tiktok',
            post_id: cid,
            record_type: 'comment',
            parent_post_id: str(c['aweme_id']),
            container_id: container.container_id,
            container_type: container.container_type,
            author_name: str(user?.['nickname']) ?? uniqueId,
            author_url: uniqueId ? `https://www.tiktok.com/@${uniqueId}` : undefined,
            ...a,
            text,
            created_at: isoFromUnix(c['create_time']),
            captured_at: ctx.captured_at,
            reactions_total: num(c['digg_count']),
            media: [],
            hashtags: extractHashtags(text),
            parser_version: tiktokModule.version,
            matched_keywords: [],
            match_score: 0,
            synced: 0,
          });
        }
      }
    }
    return [...byKey.values()];
  },
};
