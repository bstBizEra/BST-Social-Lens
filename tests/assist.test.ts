import { describe, expect, it } from 'vitest';
import { ClickLedger, DEFAULT_ASSIST, assistExhausted, classifyLabel, isCloseControl, isSamePage, newDialogs, nextClickDelay, pickExpanders, type Candidate } from '../src/lib/assist';

const PAGE = 'https://www.facebook.com/groups/123456/';

describe('classifyLabel', () => {
  it('accepts comment/reply/text expanders in English, Lao and Thai', () => {
    expect(classifyLabel('View more comments')).toBe('comments');
    expect(classifyLabel('View 12 more comments')).toBe('comments');
    expect(classifyLabel('View previous comments')).toBe('comments');
    expect(classifyLabel('View all 4 replies')).toBe('replies');
    expect(classifyLabel('View 1 reply')).toBe('replies');
    expect(classifyLabel('See more')).toBe('text');
    expect(classifyLabel('ເບິ່ງຄຳເຫັນເພີ່ມເຕີມ')).toBe('comments');
    expect(classifyLabel('ເບິ່ງ 3 ການຕອບກັບ')).toBe('replies');
    expect(classifyLabel('ເບິ່ງເພີ່ມເຕີມ')).toBe('text');
    expect(classifyLabel('ดูความคิดเห็นเพิ่มเติม')).toBe('comments');
    expect(classifyLabel('ดู 2 การตอบกลับ')).toBe('replies');
  });

  it('rejects navigation and action controls', () => {
    for (const bad of ['Join group', 'Like', 'Share', 'Reply', 'ຕອບກັບ', 'ตอบกลับ', 'See all', 'View post', 'Hide replies', 'See translation', 'Send', 'Follow', 'ເຂົ້າຮ່ວມກຸ່ມ']) {
      expect(classifyLabel(bad), bad).toBeNull();
    }
  });

  it('rejects long content and "see more" when expandText is off', () => {
    expect(classifyLabel('This post has more comments than usual, see more of them below in the thread please')).toBeNull();
    expect(classifyLabel('See more', { ...DEFAULT_ASSIST, expandText: false })).toBeNull();
  });
});

describe('isSamePage', () => {
  it('treats empty, hash and javascript hrefs as in-page', () => {
    expect(isSamePage(undefined, PAGE)).toBe(true);
    expect(isSamePage('#', PAGE)).toBe(true);
    expect(isSamePage(PAGE + '#comments', PAGE)).toBe(true);
  });
  it('rejects script-executing hrefs', () => {
    expect(isSamePage('javascript:void(0)', PAGE)).toBe(false);
    expect(isSamePage(' JavaScript:alert(1)', PAGE)).toBe(false);
    expect(isSamePage('data:text/html,x', PAGE)).toBe(false);
  });
  it('rejects links to other paths', () => {
    expect(isSamePage('/groups/123456/posts/999/', PAGE)).toBe(false);
    expect(isSamePage('https://www.facebook.com/somebody', PAGE)).toBe(false);
    expect(isSamePage('https://www.tiktok.com/@x/video/1', PAGE)).toBe(false);
  });
});

describe('pickExpanders', () => {
  const cands: Candidate[] = [
    { text: 'View more comments', role: 'button', tag: 'div' },                 // 0 ok
    { text: 'View 3 replies', role: 'button', tag: 'div', href: '/groups/123456/posts/1/' }, // 1 link → skip
    { text: 'Like', role: 'button', tag: 'div' },                               // 2 deny
    { text: 'View 2 replies', tag: 'span' },                                    // 3 not clickable
    { text: 'View 5 replies', tag: 'p', hint: 'view-more-1' },                  // 4 tiktok ok (hint + label)
    { text: 'See more', role: 'button', tag: 'div' },                           // 5 ok
    { text: 'View previous comments', tag: 'button' },                          // 6 ok
  ];
  it('keeps only same-page clickable expanders, in document order, capped per round', () => {
    const picks = pickExpanders(cands, PAGE, { ...DEFAULT_ASSIST, clicksPerRound: 3 }, 0);
    expect(picks.map((p) => p.index)).toEqual([0, 4, 5]);
    expect(picks.map((p) => p.kind)).toEqual(['comments', 'replies', 'text']);
  });
  it('never picks on control semantics alone (hint without an allow-listed label)', () => {
    const c: Candidate[] = [
      { text: 'Open profile', tag: 'p', hint: 'view-more-1' },
      { text: 'Like', role: 'button', tag: 'div', hint: 'expand' },
      { text: '', tag: 'button', hint: 'view-more-2' },
    ];
    expect(pickExpanders(c, PAGE, DEFAULT_ASSIST, 0)).toEqual([]);
  });
  it('never picks a javascript: anchor even with a good label', () => {
    const c: Candidate[] = [{ text: 'View more comments', role: 'button', tag: 'a', href: 'javascript:openThread()' }];
    expect(pickExpanders(c, PAGE, DEFAULT_ASSIST, 0)).toEqual([]);
  });
  it('honours the remaining run budget', () => {
    expect(pickExpanders(cands, PAGE, { ...DEFAULT_ASSIST, maxClicks: 10, clicksPerRound: 5 }, 9)).toHaveLength(1);
    expect(pickExpanders(cands, PAGE, { ...DEFAULT_ASSIST, maxClicks: 10 }, 10)).toHaveLength(0);
  });
});

describe('pacing + caps', () => {
  it('nextClickDelay stays within [min,max]', () => {
    for (const r of [0, 0.5, 0.999]) {
      const d = nextClickDelay(DEFAULT_ASSIST, () => r);
      expect(d).toBeGreaterThanOrEqual(DEFAULT_ASSIST.minDelayMs);
      expect(d).toBeLessThanOrEqual(DEFAULT_ASSIST.maxDelayMs);
    }
  });
  it('assistExhausted at the cap', () => {
    expect(assistExhausted(DEFAULT_ASSIST, DEFAULT_ASSIST.maxClicks - 1)).toBe(false);
    expect(assistExhausted(DEFAULT_ASSIST, DEFAULT_ASSIST.maxClicks)).toBe(true);
  });
});

describe('ClickLedger — once per run, eligible again on a new run', () => {
  it('run 1 clicks once, blocks the repeat; run 2 makes the same element eligible again', () => {
    const el = { id: 'view-more' };
    const ledger = new ClickLedger<typeof el>();
    ledger.newRun();
    expect(ledger.canClick(el)).toBe(true);
    expect(ledger.mark(el)).toBe(true); // run 1 → clicked
    expect(ledger.canClick(el)).toBe(false);
    expect(ledger.mark(el)).toBe(false); // run 1 → same element cannot click again
    ledger.newRun();
    expect(ledger.runCount).toBe(2);
    expect(ledger.canClick(el)).toBe(true); // run 2 → eligible again
    expect(ledger.mark(el)).toBe(true);
  });
});

describe('dialog handling (0.7.5): close what our click opened, never the operator\'s', () => {
  it('recognises close controls by aria-label or text in en/lo/th and refuses destructive verbs', () => {
    expect(isCloseControl(undefined, 'Close')).toBe(true);
    expect(isCloseControl('', 'ປິດ')).toBe(true);
    expect(isCloseControl('ปิด', undefined)).toBe(true);
    expect(isCloseControl('Close and delete', undefined)).toBe(false);
    expect(isCloseControl('Post', undefined)).toBe(false);
    expect(isCloseControl('Leave group', 'Close')).toBe(false);
  });
  it('newDialogs returns only dialogs absent before the click', () => {
    const a = { id: 'operator' }; const b = { id: 'ours' };
    expect(newDialogs([a], [a, b])).toEqual([b]);
    expect(newDialogs([a], [a])).toEqual([]);
    expect(newDialogs([], [b])).toEqual([b]);
  });
});
