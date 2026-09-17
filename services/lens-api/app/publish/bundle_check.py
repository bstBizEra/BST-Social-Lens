"""Export negative check (001H §4): a bundle must contain no contact values, author/contact hashes, raw keys,
text bodies, media URLs or permalinks. Pure; used by the publish transaction (hard stop) and by tests.

    check_bundle_text(text)  -> [Violation(kind, sample)]
    check_records(records)   -> [Violation]  (iterates dicts, keys and string values, recursively)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

BUNDLE_CHECK_VERSION = "1.0.0"

FORBIDDEN_KEYS = {"author_hash", "author_id", "author_name", "author_url", "contact_hash", "raw_value", "raw_value_enc",
                  "masked_value", "text", "media", "permalink", "url", "page_url", "body"}
_PHONE = re.compile(r"(?<!\d)(?:\+?856[\s-]?|0)(?:20[\s-]?\d{2}[\s-]?\d{3}[\s-]?\d{3}|20[\s-]?\d{4}[\s-]?\d{4}|(?:21|23|30|31|34|36|38|41|51|54|61|64|71|74|81|84|86)[\s-]?\d{3}[\s-]?\d{3})(?!\d)")
_MESSAGING = re.compile(r"(?:whatsapp|wa\.me|line\.me|\bline\s*id\b|ວັອດແອັບ|ໄລ\s*ໄອດີ)", re.I)
_SHA256 = re.compile(r"\b[0-9a-f]{64}\b")
_FB_URL = re.compile(r"https?://(?:[a-z]+\.)?(?:facebook\.com|fb\.com|fb\.watch|tiktok\.com)/", re.I)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
ALLOWED_HASH_KEYS = {"first_payload_hash", "checksum", "payload_hash"}   # provenance anchors are the *only* 64-hex values allowed


@dataclass(frozen=True)
class Violation:
    kind: str
    sample: str


def check_bundle_text(text: str) -> list[Violation]:
    v: list[Violation] = []
    for m in _PHONE.finditer(text):
        v.append(Violation("phone", m.group(0)[:4] + "…"))
    for m in _MESSAGING.finditer(text):
        v.append(Violation("messaging_handle", m.group(0)))
    for m in _FB_URL.finditer(text):
        v.append(Violation("platform_url", m.group(0)))
    for m in _EMAIL.finditer(text):
        v.append(Violation("email", m.group(0)[:3] + "…"))
    for k in ("author_hash", "contact_hash", "raw_value", "masked_value"):
        if re.search(rf'"{k}"\s*:', text):
            v.append(Violation("forbidden_key", k))
    return _dedupe(v)


def check_records(records: Any, _path: str = "") -> list[Violation]:
    v: list[Violation] = []
    if isinstance(records, dict):
        for k, val in records.items():
            if k in FORBIDDEN_KEYS:
                v.append(Violation("forbidden_key", f"{_path}.{k}".lstrip(".")))
                continue
            v += check_records(val, f"{_path}.{k}")
    elif isinstance(records, (list, tuple)):
        for i, item in enumerate(records):
            v += check_records(item, f"{_path}[{i}]")
    elif isinstance(records, str):
        key = _path.rsplit(".", 1)[-1].split("[")[0]
        if _SHA256.fullmatch(records) and key not in ALLOWED_HASH_KEYS:
            v.append(Violation("unexpected_hash", _path.lstrip(".")))
        v += [Violation(x.kind, f"{_path.lstrip('.')}: {x.sample}") for x in check_bundle_text(records) if x.kind != "forbidden_key"]
    return _dedupe(v)


def _dedupe(v: list[Violation]) -> list[Violation]:
    seen: set[Violation] = set()
    out: list[Violation] = []
    for x in v:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
