import { describe, expect, it } from 'vitest';
import { DEFAULT_KEYWORD_SET, matchFields, matchKeywords, type KeywordSet } from '../src/lib/keywords';

const RE = DEFAULT_KEYWORD_SET; // ດິນ ຂາຍ ເຊົ່າ ລາຄາ ບ້ານ ເມືອງ ແຂວງ

describe('matchKeywords — Lao', () => {
  it('matches a term inside a spaceless run (no word boundaries in Lao)', () => {
    const m = matchKeywords('ຂາຍດິນ ປາກເຊ', RE); // "sell-land Pakse"
    expect(m.matched).toBe(true);
    expect(m.hits).toEqual(['ດິນ', 'ຂາຍ']); // set order preserved
    expect(m.score).toBe(2);
  });

  it('does not match unrelated Lao text', () => {
    expect(matchKeywords('ສະບາຍດີ ໝູ່ເພື່ອນ', RE).matched).toBe(false);
  });

  it('respects min_hits', () => {
    const set: KeywordSet = { ...RE, min_hits: 2 };
    expect(matchKeywords('ຂາຍເຄື່ອງ', set).matched).toBe(false); // only ຂາຍ
    expect(matchKeywords('ຂາຍດິນ', set).matched).toBe(true); // ຂາຍ + ດິນ
  });

  it('exclude term rejects even when includes hit', () => {
    const set: KeywordSet = { ...RE, exclude: ['ລົດ'] }; // exclude "car"
    expect(matchKeywords('ຂາຍດິນ ບໍ່ແມ່ນ ລົດ', set).matched).toBe(false);
  });

  it('NFC-normalizes both sides', () => {
    // Decomposed vs composed forms of the same Lao string still match.
    const composed = 'ຂາຍ'.normalize('NFC');
    const decomposed = 'ຂາຍ'.normalize('NFD');
    expect(matchKeywords(decomposed, { name: 't', include: [composed] }).matched).toBe(true);
  });

  it('empty include set matches any non-empty text (capture-all)', () => {
    expect(matchKeywords('anything', { name: 'all', include: [] }).matched).toBe(true);
    expect(matchKeywords('', { name: 'all', include: [] }).matched).toBe(false);
  });

  it('mixed Latin is case-insensitive', () => {
    expect(matchKeywords('PRICE drop', { name: 't', include: ['price'] }).matched).toBe(true);
  });
});

describe('matchFields', () => {
  it('unions hits across post text and author name', () => {
    const m = matchFields(['ຂາຍດ່ວນ', 'ບ້ານ ຈັດສັນ'], RE); // text: sell; author: house
    expect(m.matched).toBe(true);
    expect(new Set(m.hits)).toEqual(new Set(['ຂາຍ', 'ບ້ານ']));
  });

  it('respects min_hits across the union', () => {
    const set: KeywordSet = { ...RE, min_hits: 2 };
    expect(matchFields(['ຂາຍ', 'ດິນ'], set).matched).toBe(true);
    expect(matchFields(['ຂາຍ', 'ສະບາຍດີ'], set).matched).toBe(false);
  });
});
