/**
 * BST Social Lens — shared types.
 *
 * The normalised record schema (v1) is deliberately platform-agnostic so the
 * ingest API, console artifact and BST agents consume one shape.
 */

export type Platform = 'facebook' | 'tiktok';

export type MediaKind = 'image' | 'video' | 'link';

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
  permalink?: string;
  /** Group id (Facebook) / hashtag or search term (TikTok) the item was seen under. */
  container_id?: string;
  container_name?: string;
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
  /** Id of the RawPayload row this was derived from. */
  raw_ref?: number;
  /** Synced to the ingest API? */
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
  | { type: 'clear'; what: 'records' | 'raw' | 'all' }
  | { type: 'sync' }
  | { type: 'getSettings' }
  | { type: 'setSettings'; settings: Partial<Settings> };

export interface Stats {
  records: Record<Platform, number>;
  raw: number;
  unsynced: number;
  lastCapture?: string;
  captureEnabled: boolean;
}
