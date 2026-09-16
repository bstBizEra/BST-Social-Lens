import { describe, expect, it } from 'vitest';
import { DEFAULT_ASSIST, assistExhausted, classifyLabel, isSamePage, nextClickDelay, pickExpanders, type Candidate } from '../src/lib/assist';

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
    expect(isSamePage('javascript:void(0)', PAGE)).toBe(true);
    expect(isSamePage(PAGE + '#comments', PAGE)).toBe(true);
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
    { text: 'View 5 replies', tag: 'p', hint: 'view-more-1' },                  // 4 tiktok ok
    { text: 'See more', role: 'button', tag: 'div' },                           // 5 ok
    { text: 'View previous comments', tag: 'button' },                          // 6 ok
  ];
  it('keeps only same-page clickable expanders, in document order, capped per round', () => {
    const picks = pickExpanders(cands, PAGE, { ...DEFAULT_ASSIST, clicksPerRound: 3 }, 0);
    expect(picks.map((p) => p.index)).toEqual([0, 4, 5]);
    expect(picks.map((p) => p.kind)).toEqual(['comments', 'replies', 'text']);
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
