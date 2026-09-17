/**
 * 001C §10.4 — the extension's keyword groups must equal the server's authoritative copy
 * (`services/lens-api/app/extract/keywords.py`), group for group, term for term, and share
 * KEYWORD_GROUPS_VERSION. The Python file is parsed textually (no Python at test time).
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { KEYWORD_GROUPS, KEYWORD_GROUPS_VERSION, keywordGroupHits, leadLabel } from '../src/lib/keywords';

const PY = readFileSync(resolve(__dirname, '../services/lens-api/app/extract/keywords.py'), 'utf8');

function parsePython(src: string): { version: string; groups: Record<string, string[]> } {
  const version = /KEYWORD_GROUPS_VERSION\s*=\s*"([^"]+)"/.exec(src)?.[1] ?? '';
  const body = /KEYWORD_GROUPS[^=]*=\s*\{([\s\S]*?)\n\}/.exec(src)?.[1] ?? '';
  const groups: Record<string, string[]> = {};
  const re = /"([a-z_]+)":\s*\(([\s\S]*?)\),?\s*(?:\n|$)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(body))) {
    const [, name, terms] = m;
    if (name && terms !== undefined) groups[name] = [...terms.matchAll(/"([^"]+)"/g)].map((x) => x[1] ?? '');
  }
  return { version, groups };
}

describe('keyword groups parity (extension ↔ lens-api)', () => {
  const py = parsePython(PY);

  it('parses the Python source', () => {
    expect(py.version).not.toBe('');
    expect(Object.keys(py.groups).length).toBe(12);
  });

  it('shares the version', () => {
    expect(KEYWORD_GROUPS_VERSION).toBe(py.version);
  });

  it('has identical groups and terms in the same order', () => {
    const ts = Object.fromEntries(Object.entries(KEYWORD_GROUPS).map(([k, v]) => [k, [...v]]));
    expect(ts).toEqual(py.groups);
  });
});

describe('keywordGroupHits / leadLabel', () => {
  it('tags a Lao sale post', () => {
    const hits = keywordGroupHits('ຂາຍດິນ 20x30 ບ້ານນາສ້າງໄຜ່ ລາຄາ 2.5 ຕື້ ໂທ 020 5512 3456');
    expect(hits.asset).toContain('ດິນ');
    expect(hits.intent_sale).toEqual(['ຂາຍ']);
    expect(hits.price).toEqual(expect.arrayContaining(['ລາຄາ', 'ຕື້']));
    expect(hits.contact).toContain('ໂທ');
    expect(leadLabel(hits)).toBe('sale');
  });

  it('weak area terms do not fire alone', () => {
    expect(keywordGroupHits('x ha rai').area).toBeUndefined();
  });

  it('returns none without an asset term', () => {
    expect(leadLabel(keywordGroupHits('ສະບາຍດີ'))).toBe('none');
    expect(leadLabel(keywordGroupHits('ໃຫ້ເຊົ່າເຮືອນ'))).toBe('rent');
    expect(leadLabel(keywordGroupHits('ຕ້ອງການຊື້ດິນ'))).toBe('wanted');
  });
});
