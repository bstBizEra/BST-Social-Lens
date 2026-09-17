/**
 * Keyword matching — Lao/Thai-aware.
 *
 * Lao script has no spaces between words and uses combining vowels/tones, so
 * word-boundary regex does NOT work (`ຂາຍດິນ` is one run that must match both
 * `ຂາຍ` and `ດິນ`). We therefore:
 *   - normalise both sides to Unicode NFC (Facebook and the keyword list can
 *     encode the same grapheme with different code-point sequences),
 *   - case-fold (only affects Latin terms),
 *   - test with substring `includes()`, never `\b`.
 * Matching runs over post text, author name and comment text — never the URL.
 */

export interface KeywordSet {
  name: string;
  /** Terms; a record matches when it contains >= min_hits distinct includes. */
  include: string[];
  /** If any exclude term is present, the record is rejected regardless of hits. */
  exclude?: string[];
  min_hits?: number;
  enabled?: boolean;
}

export const DEFAULT_KEYWORD_SET: KeywordSet = {
  name: 'real-estate-lao',
  // Lao property terms + location cues. Matching is substring: 'lat'/'long' also hit
  // 'flat'/'along' in English text — accepted for recall; tighten with min_hits if noisy.
  include: ['ດິນ', 'ຂາຍ', 'ເຊົ່າ', 'ລາຄາ', 'ເນື້ອທີ່', 'ບ້ານ', 'ເມືອງ', 'ແຂວງ', 'location', 'google map', 'lat', 'long'],
  exclude: [],
  min_hits: 1,
  enabled: true,
};

const norm = (s: string): string => s.normalize('NFC').toLowerCase();

export interface KeywordMatch {
  matched: boolean;
  hits: string[]; // distinct include terms found, in the set's order
  score: number;
}

/**
 * Match `text` against a keyword set. Empty/absent text never matches.
 * `include: []` means "match everything" (score 0) — useful for capture-all.
 */
export function matchKeywords(text: string | undefined | null, set: KeywordSet): KeywordMatch {
  if (set.enabled === false) return { matched: false, hits: [], score: 0 };
  const haystack = norm(text ?? '');
  if (set.exclude && set.exclude.some((t) => t && haystack.includes(norm(t)))) {
    return { matched: false, hits: [], score: 0 };
  }
  if (!set.include.length) return { matched: haystack.length > 0, hits: [], score: 0 };
  if (!haystack) return { matched: false, hits: [], score: 0 };
  const hits: string[] = [];
  for (const term of set.include) {
    if (term && haystack.includes(norm(term))) hits.push(term);
  }
  const min = set.min_hits ?? 1;
  return { matched: hits.length >= min, hits, score: hits.length };
}

/**
 * Match across several text fields (post text, author name, comment text) as a
 * single haystack, so min_hits and exclude apply to the union rather than to
 * any one field.
 */
export function matchFields(fields: (string | undefined | null)[], set: KeywordSet): KeywordMatch {
  const joined = fields.map((f) => f ?? '').filter(Boolean).join('\n');
  return matchKeywords(joined, set);
}
