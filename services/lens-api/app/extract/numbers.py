"""Numeric helpers for Lao/Thai/English property text (001C §5.2, §6).

Pure functions; no I/O. Evidence spans are UTF-16 code-unit offsets into the NFC text so
the extension/Portal (JavaScript) can slice them directly.
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

LAO_DIGITS = {chr(0x0ED0 + i): str(i) for i in range(10)}
THAI_DIGITS = {chr(0x0E50 + i): str(i) for i in range(10)}
_DIGIT_MAP = str.maketrans({**LAO_DIGITS, **THAI_DIGITS})

# Magnitude words → multiplier (Lao then Thai). Longer keys first when matching.
MAGNITUDES: dict[str, int] = {
    "ຕື້": 10**9,
    "ພັນລ້ານ": 10**9,
    "ຮ້ອຍລ້ານ": 10**8,
    "ສິບລ້ານ": 10**7,
    "ລ້ານ": 10**6,
    "ແສນ": 10**5,
    "ໝື່ນ": 10**4,
    "ພັນ": 10**3,
    "ล้าน": 10**6,
    "แสน": 10**5,
    "หมื่น": 10**4,
    "พัน": 10**3,
    "billion": 10**9,
    "million": 10**6,
    "mil": 10**6,
    "m": 10**6,  # only accepted directly attached to a number and followed by currency (fields.py)
    "k": 10**3,
}

CURRENCY_TERMS: dict[str, str] = {
    "ກີບ": "LAK", "₭": "LAK", "lak": "LAK", "kip": "LAK",
    "ບາດ": "THB", "฿": "THB", "บาท": "THB", "thb": "THB", "baht": "THB",
    "ໂດລາ": "USD", "$": "USD", "usd": "USD", "dollar": "USD", "us$": "USD",
}

# Area units → m² (001C §5.2)
AREA_UNITS: dict[str, Decimal] = {
    "ຕາແມັດ": Decimal(1), "ຕລມ": Decimal(1), "ແມັດກ້ອນ": Decimal(1), "m2": Decimal(1), "m²": Decimal(1),
    "sqm": Decimal(1), "sq.m": Decimal(1), "ตรม": Decimal(1), "ตารางเมตร": Decimal(1),
    "ເຮັກຕາ": Decimal(10000), "ha": Decimal(10000), "hectare": Decimal(10000), "เฮกตาร์": Decimal(10000),
    "ໄຮ່": Decimal(1600), "rai": Decimal(1600), "ไร่": Decimal(1600),
    "ງານ": Decimal(400), "ngan": Decimal(400), "งาน": Decimal(400),
    "ຕາວາ": Decimal(4), "ຕລວ": Decimal(4), "wa": Decimal(4), "ตารางวา": Decimal(4), "ตรว": Decimal(4),
}

NUMBER_RE = r"(?P<num>\d{1,3}(?:[,\s]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"


def nfc(s: str | None) -> str:
    return unicodedata.normalize("NFC", s or "")


def ascii_digits(s: str) -> str:
    """Map Lao/Thai digits to ASCII. Length-preserving, so offsets stay valid."""
    return s.translate(_DIGIT_MAP)


def parse_number(token: str) -> Decimal | None:
    t = ascii_digits(token).replace(",", "").replace(" ", "").replace(" ", "")
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def utf16_offset(text: str, code_point_index: int) -> int:
    """UTF-16 code-unit offset for a Python (code point) index."""
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text[:code_point_index])


def span16(text: str, start: int, end: int) -> tuple[int, int]:
    return utf16_offset(text, start), utf16_offset(text, end)


def slice16(text: str, start16: int, end16: int) -> str:
    """Inverse of span16 — used by tests to prove spans slice back to the value text."""
    units = text.encode("utf-16-le")
    return units[start16 * 2 : end16 * 2].decode("utf-16-le", errors="ignore")


def mag_pattern() -> str:
    keys = sorted(MAGNITUDES, key=len, reverse=True)
    return "|".join(re.escape(k) for k in keys)


def cur_pattern() -> str:
    keys = sorted(CURRENCY_TERMS, key=len, reverse=True)
    return "|".join(re.escape(k) for k in keys)


def unit_pattern() -> str:
    keys = sorted(AREA_UNITS, key=len, reverse=True)
    return "|".join(re.escape(k) for k in keys)
