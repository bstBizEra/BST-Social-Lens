"""Field extractors for RULE_V1 (001C §5.2, §5.3). Pure, deterministic, regex-based.

All functions take the NFC text and return Claim objects whose evidence spans are UTF-16
offsets into that same text. Numeric parsing runs on an ASCII-digit shadow copy of equal
length so spans remain valid for Lao/Thai digits.
"""
from __future__ import annotations

import hashlib
import re
from decimal import Decimal

from .keywords import KEYWORD_GROUPS, norm
from .models import Claim, Span
from .numbers import (
    AREA_UNITS, CURRENCY_TERMS, MAGNITUDES, NUMBER_RE, ascii_digits, cur_pattern, mag_pattern, parse_number, span16,
    unit_pattern,
)

METHOD = "RULE_V1"
RULES_VERSION = "1.0.0"

LAO_BBOX = (13.9, 22.6, 100.0, 107.8)  # lat_min, lat_max, lng_min, lng_max
LAO_LETTERS = r"຀-໿"
THAI_LETTERS = r"฀-๿"

_PRICE_TERM_RE = re.compile(r"ລາຄາ|ราคา|price", re.I)
_PER_SQM_RE = re.compile(r"(?:/|ຕໍ່|ต่อ|per)\s*(?:ຕາແມັດ|ຕລມ|m2|m²|sqm|ตรม)", re.I)
_PER_MONTH_RE = re.compile(r"(?:/|ຕໍ່|ต่อ|per|a)\s*(?:ເດືອນ|เดือน|month|mo\b)", re.I)
_PER_YEAR_RE = re.compile(r"(?:/|ຕໍ່|ต่อ|per|a)\s*(?:ປີ|ปี|year|yr\b)", re.I)
# Lao/Thai "per-unit" phrasing that precedes the number: ຕາແມັດລະ 5 ລ້ານ, ເດືອນລະ 3 ລ້ານ
_PRE_SQM_RE = re.compile(r"(?:ຕາແມັດ|ຕລມ|ແມັດ|ตรม)\s*ລະ\s*$|(?:ຕາແມັດ|ຕລມ|m2|m²|sqm)\s*(?:ລະ|ละ)\s*$", re.I)
_PRE_MONTH_RE = re.compile(r"(?:ເດືອນ|เดือน)\s*(?:ລະ|ละ)\s*$", re.I)
_PRE_YEAR_RE = re.compile(r"(?:ປີ|ปี)\s*(?:ລະ|ละ)\s*$", re.I)

# number, optional magnitude word, optional currency — or currency then number
_PRICE_RE = re.compile(
    rf"(?P<cur_pre>{cur_pattern()})?\s*{NUMBER_RE}\s*(?P<mag>{mag_pattern()})?\s*(?P<cur_post>{cur_pattern()})?",
    re.I,
)
_DIM_RE = re.compile(rf"{NUMBER_RE.replace('num', 'a')}\s*(?:x|×|X|\*|ຄູນ)\s*{NUMBER_RE.replace('num', 'b')}")
_AREA_UNIT_RE = re.compile(rf"{NUMBER_RE}\s*(?P<unit>{unit_pattern()})(?![a-z])", re.I)
_AREA_LEAD_RE = re.compile(r"ເນື້ອທີ່|ພື້ນທີ່|เนื้อที่|area|size", re.I)

# Lao numbering: mobile 020 + 8 digits (11 digits); landline 0 + 2-digit area code + 6 digits (9 digits).
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?856[\s-]?|0)20[\s-]?(?:\d{2}[\s-]?\d{3}[\s-]?\d{3}|\d{4}[\s-]?\d{4})(?!\d)"
    r"|(?<!\d)(?:\+?856[\s-]?|0)(?:21|23|30|31|34|36|38|41|51|54|61|64|71|74|81|84|86)[\s-]?\d{3}[\s-]?\d{3}(?!\d)"
)
_LINE_RE = re.compile(r"(?:line|ໄລ|ไลน์)\s*(?:id)?\s*[:：]?\s*(@?[a-z0-9._-]{3,30})", re.I)
_WHATSAPP_RE = re.compile(r"(?:whatsapp|ວັອດແອັບ|wa)\s*[:：]?\s*(\+?\d[\d\s-]{7,14}\d)", re.I)

_MAP_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:maps\.google\.[a-z.]+|google\.[a-z.]+/maps|goo\.gl/maps|maps\.app\.goo\.gl)[^\s<>\"']*",
    re.I,
)
_URL_COORD_RES = (
    re.compile(r"@(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)"),
    re.compile(r"[?&](?:q|ll|query|center)=(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)"),
    re.compile(r"!3d(-?\d{1,2}\.\d+)!4d(-?\d{1,3}\.\d+)"),
)
_COORD_RE = re.compile(r"(?<![\d.])(1[3-9]\.\d{3,}|2[0-2]\.\d{3,})\s*[, ]\s*(10[0-7]\.\d{3,})(?![\d.])")

_LOC_RES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("province", re.compile(rf"(ນະຄອນຫຼວງວຽງຈັນ|ນະຄອນຫຼວງ|ແຂວງ\s*[{LAO_LETTERS}]{{2,30}})")),
    ("district", re.compile(rf"(ເມືອງ\s*[{LAO_LETTERS}]{{2,30}})")),
    ("village", re.compile(rf"(ບ້ານ(?!ຈັດສັນ|ພ້ອມດິນ|ດ່ຽວ|ເຊົ່າ|ໃໝ່|ຫຼັງ|ສອງຊັ້ນ|ຊັ້ນດຽວ|ຕິດ|ໃກ້|ຢູ່|ພັກ)\s*[{LAO_LETTERS}]{{2,30}})")),
    ("province", re.compile(rf"(จังหวัด\s*[{THAI_LETTERS}]{{2,30}})")),
    ("village", re.compile(r"\b(ban\s+[A-Z][a-z]{2,20}(?:\s+[A-Z][a-z]{2,20})?)")),
)
_TITLE_RE = re.compile("|".join(re.escape(t) for t in sorted(KEYWORD_GROUPS["title"], key=len, reverse=True)), re.I)


def _claim(text: str, field: str, start: int, end: int, conf: float, **norm_cols) -> Claim:
    s16, e16 = span16(text, start, end)
    return Claim(
        field=field,
        value_text=text[start:end],
        evidence_span=Span(s16, e16),
        extraction_method=METHOD,
        method_version=RULES_VERSION,
        confidence=max(0.0, min(1.0, conf)),
        normalised=dict(norm_cols),
    )


# ---------------------------------------------------------------- price

def extract_price(text: str) -> list[Claim]:
    shadow = ascii_digits(text)
    out: list[Claim] = []
    dims = {(m.start(), m.end()) for m in _DIM_RE.finditer(shadow)}
    phones = {(m.start(), m.end()) for m in _PHONE_RE.finditer(shadow)}
    for m in _PRICE_RE.finditer(shadow):
        num_s, num_e = m.span("num")
        if any(a <= num_s < b for a, b in dims | phones):
            continue
        mag = m.group("mag")
        cur = m.group("cur_post") or m.group("cur_pre")
        mag_l = mag.lower() if mag else None
        # 'm'/'k' are accepted only when a currency is present (001C §5.2)
        if mag_l in {"m", "k", "mil"} and not cur:
            continue
        near_price_term = bool(_PRICE_TERM_RE.search(shadow, max(0, num_s - 14), num_s))
        if not mag and not cur and not near_price_term:
            continue
        amount = parse_number(m.group("num"))
        if amount is None:
            continue
        if mag:
            amount *= MAGNITUDES[mag_l]  # type: ignore[index]
        currency = CURRENCY_TERMS.get(norm(cur), "UNKNOWN") if cur else "UNKNOWN"
        conf = 0.90 if (mag or cur) and cur else 0.75 if mag else 0.50
        signals: list[str] = []
        if not cur and mag_l in {"ຕື້", "ລ້ານ", "ແສນ", "ໝື່ນ", "ພັນ", "ພັນລ້ານ", "ຮ້ອຍລ້ານ", "ສິບລ້ານ"}:
            currency, conf = "LAK", conf - 0.10
            signals.append("currency_inferred_lak")
        # Small bare numbers near a price term (e.g. "ລາຄາ 350") are ambiguous magnitudes.
        if not mag and not cur and amount < 100_000:
            conf -= 0.15
            signals.append("magnitude_ambiguous")
        # end = end of the last non-empty group (avoid trailing whitespace in the span)
        end = max(e for e in (m.end("num"), m.end("mag") if mag else -1, m.end("cur_post") if m.group("cur_post") else -1))
        tail = shadow[end : end + 16].lstrip()
        head = shadow[max(0, num_s - 12) : num_s]
        basis = (
            "PER_SQM" if _PER_SQM_RE.match(tail) or _PRE_SQM_RE.search(head)
            else "PER_MONTH" if _PER_MONTH_RE.match(tail) or _PRE_MONTH_RE.search(head)
            else "PER_YEAR" if _PER_YEAR_RE.match(tail) or _PRE_YEAR_RE.search(head)
            else "TOTAL"
        )
        start = m.start("cur_pre") if m.group("cur_pre") else num_s
        c = _claim(text, "PRICE", start, end, conf, amount_original=amount, currency_original=currency, price_basis=basis)
        c.signals = signals
        out.append(c)
    return _dedupe_conflicts(out, "amount_original")


# ---------------------------------------------------------------- area

def extract_area(text: str) -> list[Claim]:
    shadow = ascii_digits(text)
    out: list[Claim] = []
    for m in _DIM_RE.finditer(shadow):
        a, b = parse_number(m.group("a")), parse_number(m.group("b"))
        if a is None or b is None or not (3 <= a <= 500 and 3 <= b <= 500):
            continue
        sqm = (a * b).quantize(Decimal("0.01"))
        out.append(_claim(text, "AREA", m.start(), m.end(), 0.85, area_sqm=sqm, area_unit_original="m x m"))
        out.append(_claim(text, "FRONTAGE", m.start("a"), m.end("a"), 0.80, metres=a))
        out.append(_claim(text, "DEPTH", m.start("b"), m.end("b"), 0.80, metres=b))
    for m in _AREA_UNIT_RE.finditer(shadow):
        n = parse_number(m.group("num"))
        unit = m.group("unit")
        if n is None:
            continue
        factor = AREA_UNITS.get(unit) or AREA_UNITS.get(unit.lower())
        if factor is None:
            continue
        # 'ha'/'rai'/'wa' are weak in Latin text; require an area lead nearby or a large number
        if unit.lower() in {"ha", "rai", "wa", "ງານ"} and not _AREA_LEAD_RE.search(shadow, max(0, m.start() - 20), m.start()):
            conf = 0.60
        else:
            conf = 0.90
        sqm = (n * factor).quantize(Decimal("0.01"))
        out.append(_claim(text, "AREA", m.start(), m.end(), conf, area_sqm=sqm, area_unit_original=unit))
    return _dedupe_conflicts(out, "area_sqm", field="AREA")


# ---------------------------------------------------------------- contact

def _mask_phone(digits: str) -> str:
    return digits[:4] + "X" * max(0, len(digits) - 6) + digits[-2:] if len(digits) > 6 else "X" * len(digits)


def contact_hash(kind: str, value: str, salt: str) -> str:
    return hashlib.sha256(f"{kind}:{value}:{salt}".encode()).hexdigest()


def extract_contact(text: str, salt: str = "") -> list[Claim]:
    shadow = ascii_digits(text)
    out: list[Claim] = []
    for m in _PHONE_RE.finditer(shadow):
        digits = re.sub(r"\D", "", m.group(0))
        if digits.startswith("856"):
            digits = "0" + digits[3:]
        if len(digits) not in (9, 11):
            continue
        c = _claim(text, "CONTACT", m.start(), m.end(), 0.95, kind="PHONE", masked_value=_mask_phone(digits),
                   contact_hash=contact_hash("PHONE", digits, salt))
        c.normalised["raw_value"] = digits  # restricted column (D5); stripped before any non-reviewer read
        out.append(c)
    for m in _LINE_RE.finditer(shadow):
        v = m.group(1)
        out.append(_claim(text, "CONTACT", m.start(1), m.end(1), 0.85, kind="LINE", masked_value=v[:2] + "***",
                          contact_hash=contact_hash("LINE", v.lower(), salt), raw_value=v))
    for m in _WHATSAPP_RE.finditer(shadow):
        digits = re.sub(r"\D", "", m.group(1))
        out.append(_claim(text, "CONTACT", m.start(1), m.end(1), 0.90, kind="WHATSAPP", masked_value=_mask_phone(digits),
                          contact_hash=contact_hash("WHATSAPP", digits, salt), raw_value=digits))
    return out


# ---------------------------------------------------------------- location / map / coordinates

def extract_location_text(text: str) -> list[Claim]:
    out: list[Claim] = []
    seen: set[tuple[int, int]] = set()
    for level, rx in _LOC_RES:
        for m in rx.finditer(text):
            span = (m.start(1), m.end(1))
            if span in seen:
                continue
            seen.add(span)
            out.append(_claim(text, "LOCATION_TEXT", *span, 0.70, admin_level_hint=level))
    return out


def extract_map_and_coords(text: str) -> list[Claim]:
    out: list[Claim] = []
    covered: list[tuple[int, int]] = []
    for m in _MAP_URL_RE.finditer(text):
        url = m.group(0).rstrip(".,)")
        end = m.start() + len(url)
        covered.append((m.start(), end))
        short = bool(re.search(r"goo\.gl|maps\.app", url, re.I))
        c = _claim(text, "MAP_URL", m.start(), end, 0.95, url=url, short_link=short)
        for rx in _URL_COORD_RES:
            cm = rx.search(url)
            if cm:
                lat, lng = float(cm.group(1)), float(cm.group(2))
                if _in_laos(lat, lng):
                    c.normalised.update(lat=lat, lng=lng)
                    out.append(c)
                    out.append(_claim(text, "COORDINATE", m.start() + cm.start(), m.start() + cm.end(), 0.90, lat=lat, lng=lng, source="MAP_URL"))
                    break
                c.signals.append("point_outside_laos")
        else:
            if short:
                c.signals.append("short_map_link_unexpanded")
            out.append(c)
    shadow = ascii_digits(text)
    for m in _COORD_RE.finditer(shadow):
        if any(a <= m.start() < b for a, b in covered):
            continue
        lat, lng = float(m.group(1)), float(m.group(2))
        if _in_laos(lat, lng):
            out.append(_claim(text, "COORDINATE", m.start(), m.end(), 0.90, lat=lat, lng=lng, source="TEXT"))
    return out


def _in_laos(lat: float, lng: float) -> bool:
    a, b, c, d = LAO_BBOX
    return a <= lat <= b and c <= lng <= d


# ---------------------------------------------------------------- title mention

def extract_title_mention(text: str) -> list[Claim]:
    return [_claim(text, "LAND_TITLE_MENTION", m.start(), m.end(), 0.80) for m in _TITLE_RE.finditer(text)]


# ---------------------------------------------------------------- helpers

def _dedupe_conflicts(claims: list[Claim], key: str, field: str | None = None) -> list[Claim]:
    """Keep every claim (001A §6) but lower confidence ×0.8 when same-field values conflict > 20 %."""
    same = [c for c in claims if (field is None or c.field == field) and key in c.normalised]
    vals = [Decimal(str(c.normalised[key])) for c in same]
    if len(vals) >= 2:
        lo, hi = min(vals), max(vals)
        if lo > 0 and (hi - lo) / hi > Decimal("0.2"):
            for c in same:
                c.confidence = round(c.confidence * 0.8, 3)
                c.signals.append("conflicting_claims")
    return claims
