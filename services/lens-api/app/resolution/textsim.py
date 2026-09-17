"""Lao-safe text similarity (001E §4, §6): character 3-gram sets (no word boundaries), 64-bit SimHash, Hamming distance.

Normalisation: NFC, casefold, digits/whitespace/punctuation collapsed, URLs and phone numbers removed so
that two re-posts differing only by contact or tracking noise still match.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

_URL = re.compile(r"https?://\S+")
_PHONE = re.compile(r"(?<!\d)(?:\+?856[\s-]?|0)(?:20[\s-]?\d{2}[\s-]?\d{3}[\s-]?\d{3}|20[\s-]?\d{4}[\s-]?\d{4}|(?:21|23|30|31|34|36|38|41|51|54|61|64|71|74|81|84|86)[\s-]?\d{3}[\s-]?\d{3})(?!\d)")
_NOISE = re.compile(r"[\s\W_]+", re.UNICODE)


def normalise_text(s: str | None) -> str:
    t = unicodedata.normalize("NFC", s or "").casefold()
    t = _URL.sub(" ", t)
    t = _PHONE.sub(" ", t)
    return _NOISE.sub("", t)


def ngrams(s: str, n: int = 3) -> set[str]:
    t = normalise_text(s)
    if len(t) < n:
        return {t} if t else set()
    return {t[i : i + n] for i in range(len(t) - n + 1)}


def jaccard_3gram(a: str | None, b: str | None) -> float:
    A, B = ngrams(a or ""), ngrams(b or "")
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def simhash64(s: str | None) -> int:
    """64-bit SimHash over character 3-grams (Charikar). Deterministic (SHA-256-based feature hashes)."""
    grams = ngrams(s or "")
    if not grams:
        return 0
    v = [0] * 64
    for g in grams:
        h = int.from_bytes(hashlib.sha256(g.encode("utf-8")).digest()[:8], "big")
        for i in range(64):
            v[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(64):
        if v[i] > 0:
            out |= 1 << i
    return out


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")
