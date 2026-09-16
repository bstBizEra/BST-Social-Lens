/**
 * Assisted navigation (Layer B, ADR-0004) — pure, testable decision logic.
 *
 * Layer B may CLICK but never NAVIGATE. The only allowed targets are in-page
 * expanders on the page the operator is viewing: "View more comments",
 * "View N replies", "See more" (truncated text) and their Lao/Thai variants.
 * The interceptor captures whatever the expansion fetches — no new capture
 * path. Everything here is DOM-free so the account-safety rules (label
 * allow-list, navigation guard, caps, jitter) have unit tests.
 */

export interface AssistConfig {
  /** Master switch — off means autonomous mode is scroll-only (ADR-0003 behaviour). */
  enabled: boolean;
  /** Hard cap on expander clicks per autonomous run. */
  maxClicks: number;
  /** Max clicks per scroll round (so a long thread doesn't burst-fetch). */
  clicksPerRound: number;
  /** Min/max delay between clicks, ms (jittered). */
  minDelayMs: number;
  maxDelayMs: number;
  /** Also click "See more" text expanders (full post text). */
  expandText: boolean;
}

export const DEFAULT_ASSIST: AssistConfig = {
  enabled: false,
  maxClicks: 30,
  clicksPerRound: 3,
  minDelayMs: 1500,
  maxDelayMs: 3500,
  expandText: true,
};

/** A clickable element as seen by the content script (DOM-free projection). */
export interface Candidate {
  /** Visible text, trimmed. */
  text: string;
  /** href of the element or its closest anchor ancestor, if any. */
  href?: string;
  /** Element's role attribute, if any. */
  role?: string;
  /** Tag name, lower-case. */
  tag: string;
  /** Any data-e2e / aria-label hint (TikTok uses data-e2e="view-more-*"). */
  hint?: string;
}

export type ExpanderKind = 'comments' | 'replies' | 'text';

/*
 * Label allow-list. Matching is substring on the NFC-normalised, case-folded
 * text, same rationale as keywords.ts (Lao has no word boundaries). Keep these
 * SHORT and SPECIFIC — anything that could also describe a navigation control
 * ("See all", "View post", "View group") must not be here.
 */
const COMMENT_LABELS = [
  'view more comments', 'view previous comments', 'more comments',
  'ເບິ່ງຄຳເຫັນເພີ່ມເຕີມ', 'ຄຳເຫັນເພີ່ມເຕີມ', 'ເບິ່ງຄໍາເຫັນເພີ່ມເຕີມ',
  'ดูความคิดเห็นเพิ่มเติม', 'ความคิดเห็นเพิ่มเติม', 'ดูความคิดเห็นก่อนหน้า',
];
const REPLY_LABELS = [
  'view more replies', 'view all replies', 'replies', 'view reply', 'view 1 reply',
  'ເບິ່ງການຕອບກັບ', 'ການຕອບກັບ',
  'ดูการตอบกลับ', 'การตอบกลับ',
];
const TEXT_LABELS = ['see more', 'ເບິ່ງເພີ່ມເຕີມ', 'ดูเพิ่มเติม'];

/** Words that mark a control we must never click, even if a label above also matches. */
const DENY = [
  'join', 'like', 'share', 'send', 'post', 'reply to', 'write', 'report', 'block', 'follow',
  'see all', 'view post', 'view group', 'go to', 'open', 'login', 'log in', 'sign up',
  'delete', 'edit', 'hide', 'translation', 'translate',
  'ເຂົ້າຮ່ວມ', 'ແບ່ງປັນ', 'ສົ່ງ', 'ຕິດຕາມ', 'ລົບ',
  'เข้าร่วม', 'แชร์', 'ส่ง', 'ติดตาม', 'ลบ',
];

const norm = (s: string) => s.normalize('NFC').toLowerCase().replace(/\s+/g, ' ').trim();

/** Classify a label. Returns null when it is not an allowed expander. */
export function classifyLabel(text: string, cfg: AssistConfig = DEFAULT_ASSIST): ExpanderKind | null {
  const t = norm(text);
  if (!t || t.length > 60) return null; // expanders are short; long text is content
  if (DENY.some((d) => t.includes(d))) return null;
  // "View 3 more comments" / "View all 12 replies" — strip digits before matching.
  const stripped = t.replace(/\d[\d,.]*/g, '').replace(/\s+/g, ' ').trim();
  if (COMMENT_LABELS.some((l) => stripped.includes(l))) return 'comments';
  if (REPLY_LABELS.some((l) => stripped.includes(l)) && /repl|ຕອບ|ตอบ/.test(stripped)) return 'replies';
  if (cfg.expandText && TEXT_LABELS.some((l) => stripped === l || stripped.startsWith(l + ' ') || stripped.endsWith(' ' + l) || stripped === l + '…' || stripped === l + '...')) return 'text';
  return null;
}

/**
 * Navigation guard: an element whose href points anywhere other than the
 * current document is a link, not an expander — never click it.
 */
export function isSamePage(href: string | undefined, pageUrl: string): boolean {
  if (!href) return true;
  const h = href.trim();
  if (h === '' || h === '#' || h.startsWith('#')) return true;
  // javascript: / data: / vbscript: hrefs execute code — never "in-page" for our purposes.
  if (/^\s*(javascript|data|vbscript):/i.test(h)) return false;
  try {
    const a = new URL(h, pageUrl);
    const p = new URL(pageUrl);
    return a.origin === p.origin && a.pathname === p.pathname && a.search === p.search;
  } catch {
    return false;
  }
}

export interface Pick {
  index: number;
  kind: ExpanderKind;
}

/**
 * Select which candidates to click this round, in document order, honouring
 * `clicksPerRound` and the remaining run budget. Pure: caller passes the
 * projected candidates and the number of clicks already made.
 */
export function pickExpanders(
  candidates: Candidate[],
  pageUrl: string,
  cfg: AssistConfig,
  clicksSoFar: number,
): Pick[] {
  const budget = Math.max(0, Math.min(cfg.clicksPerRound, cfg.maxClicks - clicksSoFar));
  const out: Pick[] = [];
  for (let i = 0; i < candidates.length && out.length < budget; i++) {
    const c = candidates[i]!;
    if (!isSamePage(c.href, pageUrl)) continue;
    // Control semantics AND label must both pass: a recognised control (button role, <button>,
    // or a TikTok view-more/expand hint) carrying an allow-listed label. Neither alone is enough.
    const clickable = c.tag === 'button' || c.role === 'button' || (c.hint ?? '').includes('view-more') || (c.hint ?? '').includes('expand');
    if (!clickable) continue;
    const kind = classifyLabel(c.text, cfg);
    if (!kind) continue;
    out.push({ index: i, kind });
  }
  return out;
}

/** Random delay in [min, max], integer ms. `rnd` injectable for tests. */
export function nextClickDelay(cfg: AssistConfig, rnd: () => number = Math.random): number {
  const lo = Math.min(cfg.minDelayMs, cfg.maxDelayMs);
  const hi = Math.max(cfg.minDelayMs, cfg.maxDelayMs);
  return Math.round(lo + rnd() * (hi - lo));
}

/** True once the per-run click cap is reached. */
export function assistExhausted(cfg: AssistConfig, clicks: number): boolean {
  return clicks >= cfg.maxClicks;
}

/**
 * Per-run click ledger: an element may be clicked at most once per run, and a
 * new run starts with a clean slate (so a second run on the same page can
 * expand controls the previous run already touched — the page may have re-rendered
 * them). Object-keyed so it is testable without a DOM.
 */
export class ClickLedger<T extends object = Element> {
  private seen = new WeakSet<T>();
  private runs = 0;
  /** Start a new run — forgets every element clicked so far. */
  newRun(): void {
    this.seen = new WeakSet<T>();
    this.runs++;
  }
  canClick(el: T): boolean {
    return !this.seen.has(el);
  }
  /** Returns false if the element was already clicked this run. */
  mark(el: T): boolean {
    if (this.seen.has(el)) return false;
    this.seen.add(el);
    return true;
  }
  get runCount(): number {
    return this.runs;
  }
}
