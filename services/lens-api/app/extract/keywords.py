"""Keyword groups (SLL-PROP-DATA-001C §4.3) — the server's authoritative copy.

The extension holds a structurally identical copy in `src/lib/keywords.ts` for lead tagging
and the on-page badge; CI compares the two exports by `KEYWORD_GROUPS_VERSION` and content
(001C §10.4). Matching follows ADR-0002: NFC normalisation, case-fold, substring `in` —
never word boundaries (Lao has no spaces between words).
"""
from __future__ import annotations

import unicodedata

KEYWORD_GROUPS_VERSION = "1.0.0"

# Order matters only for reporting; membership is a set.
KEYWORD_GROUPS: dict[str, tuple[str, ...]] = {
    "asset": (
        "ດິນ", "ທີ່ດິນ", "ເຮືອນ", "ບ້ານ", "ອາຄານ", "ຕຶກ", "ຫ້ອງ", "ຄອນໂດ", "ອາພາດເມັນ", "ສາງ", "ຮ້ານ", "ສວນ",
        "ไร่", "ที่ดิน", "บ้าน", "land", "house", "condo", "shophouse",
    ),
    "intent_sale": ("ຂາຍ", "ຂາຍດ່ວນ", "ขาย", "sale", "sell"),
    "intent_rent": ("ເຊົ່າ", "ໃຫ້ເຊົ່າ", "เช่า", "rent", "lease"),
    "intent_want": ("ຊື້", "ຕ້ອງການ", "ຊອກ", "ຮັບຊື້", "ต้องการ", "wanted", "looking for"),
    "price": ("ລາຄາ", "ກີບ", "ບາດ", "ໂດລາ", "ຕື້", "ລ້ານ", "ແສນ", "usd", "$", "฿", "₭", "lak", "thb"),
    "area": ("ເນື້ອທີ່", "ຕາແມັດ", "ຕລມ", "ເຮັກຕາ", "ໄຮ່", "ງານ", "ຕາວາ", "m2", "m²", "sqm", "ha", "rai", "x", "×"),
    "location": (
        "ບ້ານ", "ເມືອງ", "ແຂວງ", "ນະຄອນຫຼວງ", "ຖະໜົນ", "ຮ່ອມ", "ຕິດ", "ໃກ້",
        "location", "google map", "map", "lat", "long",
    ),
    "agent": ("ນາຍໜ້າ", "ບໍລິການ", "agent", "broker", "ຕົວແທນ"),
    "owner": ("ເຈົ້າຂອງ", "ເຈົ້າຂອງຂາຍເອງ", "ບໍ່ຜ່ານນາຍໜ້າ", "owner", "direct owner"),
    "project": ("ໂຄງການ", "project", "phase", "ເຟສ", "unit", "ຫຼັງ"),
    "title": ("ໃບຕາດິນ", "ໃບຕາດິນແທ້", "ໂສມ", "title deed", "ນສ3", "ໂສມແດງ"),
    "contact": ("ໂທ", "ຕິດຕໍ່", "tel", "call", "whatsapp", "line", "ວັອດແອັບ", "ໄລ"),
}

# Terms that pull a record away from property (001C §5.1 adjustments).
EXCLUDE_TERMS: tuple[str, ...] = ("ລົດ", "ໂທລະສັບ", "ວຽກ", "ຮັບສະໝັກ", "ໄອໂຟນ", "iphone", "ລົດຈັກ")

# Weak single-character-ish terms that must not fire on their own for the `area` group
# (`x`, `ha`, `rai` appear in ordinary text); they count only next to a number (fields.py).
AREA_WEAK_TERMS = frozenset({"x", "×", "ha", "rai", "ງານ"})


def norm(s: str | None) -> str:
    return unicodedata.normalize("NFC", s or "").casefold()


def group_hits(text: str | None) -> dict[str, list[str]]:
    """Return {group: [terms found]} for groups with ≥ 1 strong hit. Deterministic order."""
    hay = norm(text)
    out: dict[str, list[str]] = {}
    if not hay:
        return out
    for group, terms in KEYWORD_GROUPS.items():
        hits = [t for t in terms if t and (t not in AREA_WEAK_TERMS or group != "area") and norm(t) in hay]
        if hits:
            out[group] = hits
    return out


def exclude_hits(text: str | None) -> list[str]:
    hay = norm(text)
    return [t for t in EXCLUDE_TERMS if norm(t) in hay] if hay else []
