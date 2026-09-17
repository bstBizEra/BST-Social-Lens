"""In-memory gazetteer for GEO_RULE_V1's text path (001D §4.2).

Built from the `geo.*` tables (or a JSON fixture in tests). Pure Python: NFC + tone-mark-
insensitive matching, alias lookup, and a character-bigram similarity that stands in for
`pg_trgm` until PostGIS/pg_trgm run on the cluster (G1). Codes are opaque upstream codes.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Lao tone marks + a few combining signs stripped for matching only (U+0EC8–U+0ECD).
_LAO_TONES = re.compile(r"[່-ໍຼ]")
_PREFIXES = {
    "province": ("ນະຄອນຫຼວງ", "ແຂວງ", "ຂ.", "จังหวัด", "province", "prefecture"),
    "district": ("ເມືອງ", "ມ.", "อำเภอ", "district", "muang", "mueang"),
    "village": ("ບ້ານ", "ບ.", "หมู่บ้าน", "ban", "village"),
}


def normalise_name(s: str | None, level: str | None = None) -> str:
    t = unicodedata.normalize("NFC", s or "").casefold().strip()
    t = _LAO_TONES.sub("", t)
    if level:
        for p in _PREFIXES[level]:
            p = _LAO_TONES.sub("", p.casefold())
            if t.startswith(p):
                t = t[len(p):].strip()
                break
    return re.sub(r"[\s\-_.]+", "", t)


def bigrams(s: str) -> set[str]:
    return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def similarity(a: str, b: str) -> float:
    """Dice coefficient on character bigrams — Lao-safe (no word boundaries needed)."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    A, B = bigrams(a), bigrams(b)
    return 2 * len(A & B) / (len(A) + len(B))


@dataclass
class Unit:
    level: str
    code: str
    name_lo: str
    name_en: str | None = None
    parent_code: str | None = None       # district → province; village → district
    province_code: str | None = None     # convenience for villages
    centroid: tuple[float, float] | None = None
    variants: list[str] = field(default_factory=list)


@dataclass
class Gazetteer:
    admin_version: str | None
    units: dict[str, dict[str, Unit]] = field(default_factory=lambda: {"province": {}, "district": {}, "village": {}})
    _index: dict[str, dict[str, list[Unit]]] = field(default_factory=lambda: {"province": {}, "district": {}, "village": {}})

    # ------------------------------------------------------------ build

    def add(self, u: Unit, aliases: list[str] | None = None) -> None:
        self.units[u.level][u.code] = u
        names = [u.name_lo] + ([u.name_en] if u.name_en else []) + list(u.variants) + list(aliases or [])
        for n in names:
            # index both the full form (prefix kept) and the bare form (prefix stripped)
            for k in {normalise_name(n, None), normalise_name(n, u.level)}:
                if k:
                    bucket = self._index[u.level].setdefault(k, [])
                    if all(x.code != u.code for x in bucket):
                        bucket.append(u)

    @classmethod
    def from_rows(cls, admin_version: str | None, provinces: list[dict], districts: list[dict], villages: list[dict], aliases: list[dict] | None = None) -> "Gazetteer":
        g = cls(admin_version)
        by_code: dict[tuple[str, str], list[str]] = {}
        for a in aliases or []:
            by_code.setdefault((a["level"], a["code"]), []).append(a["alias"])
        for r in provinces:
            g.add(Unit("province", r["code"], r["name_lo"], r.get("name_en"), None, r["code"], _c(r), list(r.get("name_variants") or [])), by_code.get(("province", r["code"])))
        for r in districts:
            g.add(Unit("district", r["code"], r["name_lo"], r.get("name_en"), r["province_code"], r["province_code"], _c(r), list(r.get("name_variants") or [])), by_code.get(("district", r["code"])))
        for r in villages:
            g.add(Unit("village", r["code"], r["name_lo"], r.get("name_en"), r["district_code"], r["province_code"], _c(r), list(r.get("name_variants") or [])), by_code.get(("village", r["code"])))
        return g

    @classmethod
    def from_fixture(cls, path: Path) -> "Gazetteer":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_rows(d.get("admin_version"), d["provinces"], d["districts"], d["villages"], d.get("aliases"))

    @property
    def empty(self) -> bool:
        return not any(self.units.values())

    # ------------------------------------------------------------ lookup

    def exact(self, level: str, text: str) -> list[Unit]:
        """Full form first (e.g. ນະຄອນຫຼວງວຽງຈັນ ≠ ວຽງຈັນ), then the prefix-stripped form."""
        full = self._index[level].get(normalise_name(text, None), [])
        if full:
            return list(full)
        return list(self._index[level].get(normalise_name(text, level), []))

    def fuzzy(self, level: str, text: str, threshold: float = 0.6, limit: int = 5) -> list[tuple[Unit, float]]:
        key = normalise_name(text, level)
        if len(key) < 3:
            return []
        scored: list[tuple[Unit, float]] = []
        seen: set[str] = set()
        for k, units in self._index[level].items():
            s = similarity(key, k)
            if s >= threshold:
                for u in units:
                    if u.code not in seen:
                        seen.add(u.code)
                        scored.append((u, s))
        scored.sort(key=lambda x: (-x[1], x[0].code))
        return scored[:limit]

    def parent(self, u: Unit) -> Unit | None:
        if u.level == "village":
            return self.units["district"].get(u.parent_code or "")
        if u.level == "district":
            return self.units["province"].get(u.parent_code or "")
        return None


def _c(r: dict[str, Any]) -> tuple[float, float] | None:
    lat, lng = r.get("centroid_lat"), r.get("centroid_lng")
    return (float(lat), float(lng)) if lat is not None and lng is not None else None
