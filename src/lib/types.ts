/**
 * BST Social Lens — shared types.
 *
 * The normalised record schema (v1) is deliberately platform-agnostic so the
 * ingest API, console artifact and BST agents consume one shape.
 */

import { DEFAULT_AUTORUN, type AutoRunConfig } from './autorun';
import { DEFAULT_KEYWORD_SET, type KeywordSet } from './keywords';
export type { KeywordSet } from './keywords';
export type { AutoRunConfig } from './autorun';

export type Platform = 'facebook' | 'tiktok';

export type MediaKind = 'image' | 'video' | 'link';

/** Post vs comment. Comments carry `parent_post_id`. */
export type RecordType = 'post' | 'comment';

/** Where the item was seen. Derived from the payload, used as a query filter. */
export type ContainerType = 'group' | 'page' | 'profile' | 'feed' | 'hashtag' | 'search' | 'unknown';

/** Which text triggered the keyword match. */
export type MatchedVia = 'post' | 'comment' | 'author';

export interface Media {
  kind: MediaKind;
  url: string;
  thumbnail?: string;
}

export interface SocialRecord {
  /** `${platform}:${post_id}` — primary key, used for dedupe. */
  key: string;
  platform: Platform;
  post_id: string;
  /** post (default) or comment. */
  record_type: RecordType;
  /** For comments: the post_id they belong to. */
  parent_post_id?: string;
  permalink?: string;
  /** Group id (Facebook) / hashtag or search term (TikTok) the item was seen under. */
  container_id?: string;
  container_name?: string;
  /** group | page | profile | feed | hashtag | search | unknown. */
  container_type?: ContainerType;
  author_name?: string;
  /** Raw author id (kept only when `settings.hashAuthorIds` is off). */
  author_id?: string;
  /** SHA-256 of platform:author_id — always populated when an id is known. */
  author_hash?: string;
  author_url?: string;
  text?: string;
  lang?: string;
  created_at?: string; // ISO-8601
  captured_at: string; // ISO-8601
  reactions_total?: number;
  reactions_breakdown?: Record<string, number>;
  comments_count?: number;
  shares_count?: number;
  views_count?: number;
  media: Media[];
  hashtags: string[];
  /** Parser version that produced this record — bump when a module changes. */
  parser_version: string;
  /** Distinct keyword-set terms this record matched. */
  matched_keywords: string[];
  /** Count of distinct matched terms (0 when capturing all). */
  match_score: number;
  /** Which field triggered the match (post text, a comment, or author name). */
  matched_via?: MatchedVia;
  /** SHA-256 of the normalized permalink — links this record to the seen frontier. */
  url_hash?: string;
  /** Id of the RawPayload row this was derived from. */
  raw_ref?: number;
  /** SHA-256 of the raw payload body this record was parsed from (L0 link, Phase 5). */
  payload_hash?: string;
  /** SHA-256 of the record's content fields (text, dates, permalink, media, hashtags, author_hash). */
  content_hash?: string;
  /** Synced to the ingest API? */
  synced: 0 | 1;
}

/** Seen-link frontier row — prevents re-opening the same permalink/link. */
export interface SeenLink {
  /** SHA-256 of the normalized URL — primary key. */
  url_hash: string;
  url: string;
  platform: Platform | 'unknown';
  first_seen: string; // ISO
  last_status: 'seen' | 'queued' | 'fetched' | 'failed' | 'skipped';
  fetch_count: number;
  /** Optional: allow a re-visit after this time (prices change). */
  refresh_after?: string;
  synced: 0 | 1;
}

export interface RawPayload {
  id?: number;
  platform: Platform | 'unknown';
  url: string;
  method: string;
  status: number;
  /** Where the payload came from. */
  source: 'fetch' | 'xhr' | 'embedded' | 'dom';
  page_url: string;
  captured_at: string;
  /** Response body (text). Trimmed to `settings.maxRawBytes`. */
  body: string;
  parsed_count: number;
  parse_error?: string;
  /** SHA-256 of `body` as stored (after any truncation) — the L0 identity. */
  payload_hash?: string;
  /** Body was cut to `maxRawBytes`. */
  truncated?: boolean;
  /** Pushed to POST /raw? (0 = no, 1 = yes, 2 = rejected/too large — do not retry). */
  synced?: 0 | 1 | 2;
}

export interface CaptureRun {
  id?: number;
  platform: Platform;
  page_url: string;
  started_at: string;
  ended_at?: string;
  records: number;
  payloads: number;
}

export interface Settings {
  captureEnabled: boolean;
  hashAuthorIds: boolean;
  keepRawPayloads: boolean;
  maxRawBytes: number;
  rawRetentionDays: number;
  ingestUrl: string;
  ingestToken: string;
  autoSync: boolean;
  /** 'matched' stores only keyword hits; 'all' stores everything, still tagged. */
  storeMode: 'matched' | 'all';
  /** Active keyword set applied at capture. */
  keywordSet: KeywordSet;
  /** Also parse and store comments (not just posts). */
  captureComments: boolean;
  /** Autonomous auto-scroll pacing + caps. */
  autoRun: AutoRunConfig;
  /** Phase 5: push raw payloads (L0 evidence) to the server alongside records. */
  sendRaw: boolean;
}

export const DEFAULT_SETTINGS: Settings = {
  captureEnabled: true,
  hashAuthorIds: true,
  keepRawPayloads: true,
  maxRawBytes: 2_000_000,
  rawRetentionDays: 30,
  ingestUrl: import.meta.env.WXT_INGEST_URL ?? 'http://localhost:7710/ingest',
  ingestToken: import.meta.env.WXT_INGEST_TOKEN ?? '',
  autoSync: false,
  storeMode: 'matched',
  keywordSet: DEFAULT_KEYWORD_SET,
  captureComments: true,
  autoRun: DEFAULT_AUTORUN,
  sendRaw: true,
};

/* ---------- Messages: page (MAIN world) → bridge (isolated) → background ---------- */

export const PAGE_MESSAGE_SOURCE = 'bst-social-lens' as const;

/** Posted with window.postMessage from the MAIN-world interceptor. */
export interface PageCaptureMessage {
  source: typeof PAGE_MESSAGE_SOURCE;
  type: 'capture';
  payload: {
    url: string;
    method: string;
    status: number;
    kind: 'fetch' | 'xhr' | 'embedded';
    body: string;
    page_url: string;
    ts: number;
  };
}

/** runtime messages understood by the background service worker. */
export type RuntimeMessage =
  | { type: 'capture'; payload: PageCaptureMessage['payload'] }
  | { type: 'stats' }
  | { type: 'export'; format: 'ndjson' | 'csv'; platform?: Platform }
  | { type: 'exportRaw'; platform?: Platform; limit?: number }
  | { type: 'clear'; what: 'records' | 'raw' | 'seen' | 'all' }
  | { type: 'sync' }
  | { type: 'hostPermission'; request: boolean }
  | { type: 'getSettings' }
  | { type: 'setSettings'; settings: Partial<Settings> }
  /** Is this normalized URL already in the frontier? */
  | { type: 'seenCheck'; url: string }
  /** Mark a URL as seen/queued/fetched in the frontier. */
  | { type: 'seenMark'; url: string; status: SeenLink['last_status']; platform?: Platform }
  /** Autonomous mode: start/stop auto-scroll on the active tab (from side panel). */
  | { type: 'autoStart' }
  | { type: 'autoStop' }
  /** Side panel polls the background for the latest auto-run status. */
  | { type: 'autoState' }
  /** Progress ping from the auto-scroll content script → background. */
  | { type: 'autoProgress'; progress: AutoProgress };

export interface AutoProgress {
  running: boolean;
  scrolls: number;
  reason: string | null;
}

export interface Stats {
  records: Record<Platform, number>;
  comments: number;
  matched: number;
  seen: number;
  raw: number;
  unsynced: number;
  rawUnsynced: number;
  lastCapture?: string;
  captureEnabled: boolean;
  storeMode: Settings['storeMode'];
}
