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
  include: ['ດິນ', 'ຂາຍ', 'ເຊົ່າ', 'ລາຄາ', 'ບ້ານ', 'ເມືອງ', 'ແຂວງ'],
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

// ---------------------------------------------------------------------------
// Keyword groups (SLL-PROP-DATA-001C §4.3). Mirror of the server's authoritative copy in
// `services/lens-api/app/extract/keywords.py`; `tests/keyword-groups-parity.test.ts` fails
// CI when the two drift. Used on the client for lead tagging and the on-page badge only —
// classification is a server-side (L2) concern.
// ---------------------------------------------------------------------------

export const KEYWORD_GROUPS_VERSION = '1.0.0';

export type KeywordGroupName =
  | 'asset' | 'intent_sale' | 'intent_rent' | 'intent_want' | 'price' | 'area'
  | 'location' | 'agent' | 'owner' | 'project' | 'title' | 'contact';

export const KEYWORD_GROUPS: Record<KeywordGroupName, readonly string[]> = {
  asset: [
    'ດິນ', 'ທີ່ດິນ', 'ເຮືອນ', 'ບ້ານ', 'ອາຄານ', 'ຕຶກ', 'ຫ້ອງ', 'ຄອນໂດ', 'ອາພາດເມັນ', 'ສາງ', 'ຮ້ານ', 'ສວນ',
    'ไร่', 'ที่ดิน', 'บ้าน', 'land', 'house', 'condo', 'shophouse',
  ],
  intent_sale: ['ຂາຍ', 'ຂາຍດ່ວນ', 'ขาย', 'sale', 'sell'],
  intent_rent: ['ເຊົ່າ', 'ໃຫ້ເຊົ່າ', 'เช่า', 'rent', 'lease'],
  intent_want: ['ຊື້', 'ຕ້ອງການ', 'ຊອກ', 'ຮັບຊື້', 'ต้องการ', 'wanted', 'looking for'],
  price: ['ລາຄາ', 'ກີບ', 'ບາດ', 'ໂດລາ', 'ຕື້', 'ລ້ານ', 'ແສນ', 'usd', '$', '฿', '₭', 'lak', 'thb'],
  area: ['ເນື້ອທີ່', 'ຕາແມັດ', 'ຕລມ', 'ເຮັກຕາ', 'ໄຮ່', 'ງານ', 'ຕາວາ', 'm2', 'm²', 'sqm', 'ha', 'rai', 'x', '×'],
  location: [
    'ບ້ານ', 'ເມືອງ', 'ແຂວງ', 'ນະຄອນຫຼວງ', 'ຖະໜົນ', 'ຮ່ອມ', 'ຕິດ', 'ໃກ້',
    'location', 'google map', 'map', 'lat', 'long',
  ],
  agent: ['ນາຍໜ້າ', 'ບໍລິການ', 'agent', 'broker', 'ຕົວແທນ'],
  owner: ['ເຈົ້າຂອງ', 'ເຈົ້າຂອງຂາຍເອງ', 'ບໍ່ຜ່ານນາຍໜ້າ', 'owner', 'direct owner'],
  project: ['ໂຄງການ', 'project', 'phase', 'ເຟສ', 'unit', 'ຫຼັງ'],
  title: ['ໃບຕາດິນ', 'ໃບຕາດິນແທ້', 'ໂສມ', 'title deed', 'ນສ3', 'ໂສມແດງ'],
  contact: ['ໂທ', 'ຕິດຕໍ່', 'tel', 'call', 'whatsapp', 'line', 'ວັອດແອັບ', 'ໄລ'],
};

/** Weak `area` terms that only count next to a number on the server; ignored for lead tagging here. */
const AREA_WEAK = new Set(['x', '×', 'ha', 'rai', 'ງານ']);

/** Which groups fire on a text (lead tagging for the badge). Same matching rules as `matchKeywords`. */
export function keywordGroupHits(text: string | undefined | null): Partial<Record<KeywordGroupName, string[]>> {
  const hay = norm(text ?? '');
  const out: Partial<Record<KeywordGroupName, string[]>> = {};
  if (!hay) return out;
  for (const [group, terms] of Object.entries(KEYWORD_GROUPS) as [KeywordGroupName, readonly string[]][]) {
    const hits = terms.filter((t) => !(group === 'area' && AREA_WEAK.has(t)) && hay.includes(norm(t)));
    if (hits.length) out[group] = hits;
  }
  return out;
}

/** Coarse lead label for the on-page badge — NOT a classification (that is L2, server-side). */
export function leadLabel(hits: Partial<Record<KeywordGroupName, string[]>>): 'sale' | 'rent' | 'wanted' | 'property' | 'none' {
  if (!hits.asset) return 'none';
  if (hits.intent_sale && !hits.intent_rent) return 'sale';
  if (hits.intent_rent && !hits.intent_sale) return 'rent';
  if (hits.intent_want) return 'wanted';
  return 'property';
}
