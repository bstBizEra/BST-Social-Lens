"""GEO_RULE_V1 — location resolver (001D §4–§6). Pure: claims + gazetteer in, resolutions out.

Text path (B) is fully implemented against the in-memory gazetteer. Point path (A) keeps the
advertiser's point with its precision; without PostGIS polygons (G1 pending) admin codes on a
point row come only from agreement with the text path, and that approximation is named in
`signals` (`point_near_text_centroid` / `point_far_from_text_centroid`) so nothing is silent.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..extract.models import Claim
from .gazetteer import Gazetteer, Unit

METHOD = "GEO_RULE_V1"
RESOLVER_VERSION = "1.0.0"

PRECISION_OF_LEVEL = {"village": "VILLAGE", "district": "DISTRICT", "province": "PROVINCE"}
CENTROID_SOURCE = {"village": "VILLAGE_CENTROID", "district": "DISTRICT_CENTROID", "province": "PROVINCE_CENTROID"}
BASE = {"point": 0.90, "village_exact": 0.85, "village_fuzzy": 0.60, "district_exact": 0.80, "district_fuzzy": 0.55,
        "province_exact": 0.80, "province_fuzzy": 0.55, "text_only": 0.30}
LOW_CONFIDENCE = 0.5
AGREE_KM, CONFLICT_KM = 12.0, 30.0
_ZOOM_RE = re.compile(r",(\d{1,2})(?:\.\d+)?z\b")


@dataclass
class Resolution:
    precision: str
    point_source: str = "NONE"
    lat: float | None = None
    lng: float | None = None
    province_code: str | None = None
    district_code: str | None = None
    village_code: str | None = None
    confidence: float = 0.0
    signals: list[str] = field(default_factory=list)
    input_claim_indexes: list[int] = field(default_factory=list)
    is_primary: bool = False
    review_status: str = "UNREVIEWED"
    resolver_method: str = METHOD
    resolver_version: str = RESOLVER_VERSION

    def finalise(self) -> "Resolution":
        self.confidence = round(max(0.0, min(1.0, self.confidence)), 3)
        if self.confidence < LOW_CONFIDENCE and self.review_status == "UNREVIEWED":
            self.review_status = "LOW_CONFIDENCE"
        return self


def resolve(claims: list[Claim], gaz: Gazetteer | None) -> list[Resolution]:
    """Resolve one observation's location claims. Returns [] when the observation has no location claims."""
    idx_text = [i for i, c in enumerate(claims) if c.field == "LOCATION_TEXT"]
    idx_coord = [i for i, c in enumerate(claims) if c.field == "COORDINATE"]
    idx_map = [i for i, c in enumerate(claims) if c.field == "MAP_URL"]
    if not (idx_text or idx_coord or idx_map):
        return []

    out: list[Resolution] = []
    text_res = _text_path(claims, idx_text, gaz) if idx_text else None
    point_res = _point_path(claims, idx_coord, idx_map)

    if point_res and text_res and text_res.precision not in ("TEXT_ONLY", "UNKNOWN"):
        _reconcile(point_res, text_res, gaz)
    if point_res:
        out.append(point_res)
    if text_res:
        out.append(text_res)
    if not out and idx_map:  # only an unexpanded short link
        r = Resolution("TEXT_ONLY", confidence=BASE["text_only"], input_claim_indexes=idx_map, signals=["short_map_link_unexpanded"])
        out.append(r)
    for r in out:
        r.finalise()
    best = max(out, key=lambda r: (r.confidence, r.point_source in ("MAP_URL", "TEXT_COORDINATE")))
    best.is_primary = True
    return out


# ---------------------------------------------------------------- text path (B)

def _text_path(claims: list[Claim], idx: list[int], gaz: Gazetteer | None) -> Resolution:
    res = Resolution("TEXT_ONLY", input_claim_indexes=list(idx), confidence=BASE["text_only"])
    if gaz is None or gaz.empty:
        res.signals.append("no_gazetteer")
        return res
    by_level: dict[str, list[tuple[int, str]]] = {"province": [], "district": [], "village": []}
    for i in idx:
        hint = claims[i].normalised.get("admin_level_hint") or "village"
        by_level.get(hint, by_level["village"]).append((i, claims[i].value_text))

    province = _match_level(gaz, "province", by_level["province"], res, None)
    district = _match_level(gaz, "district", by_level["district"], res, province)
    village = _match_level(gaz, "village", by_level["village"], res, district or province)

    # hierarchy consistency (001D §4.2 step 3)
    if village:
        d = gaz.parent(village)
        if district and d and d.code != district.code:
            res.signals.append("hierarchy_conflict:village_district")
            res.confidence *= 0.8
        if not district and d:
            district = d
            res.signals.append("district_from_village")
        p = gaz.parent(district) if district else None
        if province and p and p.code != province.code:
            res.signals.append("hierarchy_conflict:district_province")
            res.confidence *= 0.8
        if not province and p:
            province = p
            res.signals.append("province_from_district")
    elif district:
        p = gaz.parent(district)
        if province and p and p.code != province.code:
            res.signals.append("hierarchy_conflict:district_province")
            res.confidence *= 0.8
        if not province and p:
            province = p
            res.signals.append("province_from_district")

    deepest = village or district or province
    if not deepest:
        res.signals.append("no_gazetteer_match")
        return res
    res.precision = PRECISION_OF_LEVEL[deepest.level]
    res.province_code = province.code if province else None
    res.district_code = district.code if district else None
    res.village_code = village.code if village else None
    if deepest.centroid:
        res.lat, res.lng = deepest.centroid
        res.point_source = CENTROID_SOURCE[deepest.level]
    return res


def _match_level(gaz: Gazetteer, level: str, mentions: list[tuple[int, str]], res: Resolution, parent: Unit | None) -> Unit | None:
    """Pick one unit for a level from its text mentions; sets confidence for the deepest successful level."""
    if not mentions:
        return None
    for _, text in mentions:
        cands = gaz.exact(level, text)
        kind = "exact"
        if not cands:
            fz = gaz.fuzzy(level, text)
            cands = [u for u, _ in fz]
            kind = "fuzzy"
            if cands:
                res.signals.append(f"{level}_fuzzy:{fz[0][1]:.2f}")
        if not cands:
            res.signals.append(f"{level}_unmatched")
            continue
        if len(cands) > 1 and parent:
            narrowed = [u for u in cands if _within(gaz, u, parent)]
            if narrowed:
                res.signals.append(f"{level}_disambiguated_by_{parent.level}")
                cands = narrowed
        if len(cands) > 1:
            res.signals.append(f"{level}_ambiguous:" + ",".join(u.code for u in cands[:5]))
            # deepest unambiguous level wins: report candidates, do not pick (001D §4.2 step 4)
            continue
        u = cands[0]
        res.signals.append(f"{level}_{kind}")
        res.confidence = BASE[f"{level}_{kind}"]
        return u
    return None


def _within(gaz: Gazetteer, u: Unit, parent: Unit) -> bool:
    if parent.level == "district":
        return u.parent_code == parent.code
    if parent.level == "province":
        return u.province_code == parent.code
    return False


# ---------------------------------------------------------------- point path (A)

def _point_path(claims: list[Claim], idx_coord: list[int], idx_map: list[int]) -> Resolution | None:
    if not idx_coord:
        return None
    i = idx_coord[0]
    c = claims[i]
    lat, lng = float(c.normalised["lat"]), float(c.normalised["lng"])
    src = "MAP_URL" if c.normalised.get("source") == "MAP_URL" else "TEXT_COORDINATE"
    res = Resolution("EXACT_COORDINATE", src, lat, lng, confidence=BASE["point"], input_claim_indexes=[i] + idx_map)
    if src == "MAP_URL":
        url = next((claims[j].normalised.get("url", "") for j in idx_map), "")
        m = _ZOOM_RE.search(url)
        if m and "/place/" not in url and "q=" not in url:
            z = int(m.group(1))
            if z < 15:  # viewport, not a pin: scale ~100 m at z≥12, coarser below (confidence says so; DDL ties point precisions to point sources)
                res.precision = "PARCEL_APPROXIMATE"
                res.confidence -= 0.10 if z >= 12 else 0.25
                res.signals.append(f"viewport_only_zoom:{z}")
    res.signals.append("no_polygons")  # PostGIS point-in-polygon pending (G1)
    return res


# ---------------------------------------------------------------- reconcile (C)

def _reconcile(point: Resolution, text: Resolution, gaz: Gazetteer | None) -> None:
    if text.lat is None or text.lng is None:
        return
    km = _haversine_km(point.lat, point.lng, text.lat, text.lng)  # type: ignore[arg-type]
    if km <= AGREE_KM:
        point.signals.append(f"point_near_text_centroid:{km:.1f}km")
        point.province_code, point.district_code, point.village_code = text.province_code, text.district_code, text.village_code
        point.confidence = min(1.0, point.confidence + 0.10)
        text.signals.append("point_text_agree")
        text.confidence = min(1.0, text.confidence + 0.10)
    elif km >= CONFLICT_KM:
        point.signals.append(f"point_far_from_text_centroid:{km:.0f}km")
        text.signals.append("conflict_text_vs_point")
        point.confidence -= 0.15
        text.confidence -= 0.15
        point.review_status = text.review_status = "LOW_CONFIDENCE"
    else:
        point.signals.append(f"point_text_undetermined:{km:.0f}km")


def apply_polygon_lookup(resolutions: list[Resolution], lookups: dict[int, dict | None]) -> None:
    """Fill admin codes on point rows from a PostGIS point-in-polygon result (001D §4.1 step 3), replacing the
    `no_polygons` marker. `lookups` maps the index of a point resolution → geo.point_in_admin row (or None)."""
    for i, res in enumerate(resolutions):
        if res.point_source not in ("MAP_URL", "TEXT_COORDINATE"):
            continue
        hit = lookups.get(i)
        if hit is None:
            continue
        res.signals = [s for s in res.signals if s != "no_polygons"]
        if not (hit.get("province_code") or hit.get("district_code") or hit.get("village_code")):
            res.signals.append("point_outside_admin_polygons")
            continue
        text_rows = [r for r in resolutions if r.point_source not in ("MAP_URL", "TEXT_COORDINATE") and r.district_code]
        res.province_code, res.district_code, res.village_code = hit.get("province_code"), hit.get("district_code"), hit.get("village_code")
        res.signals = [s for s in res.signals if not s.startswith("point_near_text_centroid") and not s.startswith("point_text_undetermined")]
        res.signals.append("st_within:" + (hit.get("village_code") or hit.get("district_code") or hit.get("province_code") or "?"))
        for t in text_rows:
            if t.district_code == res.district_code:
                if "point_text_agree" not in t.signals:
                    t.signals.append("point_text_agree")
            elif t.district_code and res.district_code and "conflict_text_vs_point" not in t.signals:
                t.signals.append("conflict_text_vs_point")
                t.confidence = round(max(0.0, t.confidence - 0.15), 3)
                t.review_status = "LOW_CONFIDENCE" if t.confidence < LOW_CONFIDENCE else t.review_status


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
