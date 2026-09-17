"""MATCH_V1 — pair scorer for same-property resolution (001E §6). Pure and explainable.

    score_pair(a: Side, b: Side) -> Score(total, decision, signals=[Signal(name, contribution, evidence)], forced)

Weights are the foundation-notes starting points and are **uncalibrated** (E1) until the §11 reviewed
sample exists; `MATCH_VERSION` changes when they do. Every fired signal is returned with its
contribution so a stored decision can always be re-derived (001E §11.2).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

from .textsim import hamming, jaccard_3gram, simhash64

MATCH_VERSION = "1.0.0-uncalibrated"
PRECISION_RANK = {"EXACT_COORDINATE": 6, "PARCEL_APPROXIMATE": 5, "VILLAGE": 4, "DISTRICT": 3, "PROVINCE": 2, "TEXT_ONLY": 1, "UNKNOWN": 0}

WEIGHTS: dict[str, int] = {
    "coord_exact": 30, "coord_near": 25, "map_same": 25, "area_match": 15, "area_close": 8, "contact_same": 15,
    "image_match": 15, "village_same": 8, "district_same": 3, "text_similar": 8, "frontage_depth_match": 8,
    "price_close": 5, "asset_type_same": 3, "temporal_window": 2, "same_cluster": 10,
    # penalties
    "asset_type_conflict": -25, "area_conflict": -20, "location_conflict": -40, "transaction_conflict": -15,
}
HIGH, REVIEW = 80, 60
HAVERSINE_TAG = "approx:haversine"  # E6 — until PostGIS


@dataclass
class Side:
    """The subset of an observation MATCH_V1 needs. Missing fields simply do not fire their signals."""
    observation_id: int | str
    signal_class: str                       # PROPERTY_SALE | PROPERTY_RENT
    asset_type: str = "UNKNOWN"
    asset_confidence: float = 0.0
    precision: str = "UNKNOWN"
    lat: float | None = None
    lng: float | None = None
    village_code: str | None = None
    district_code: str | None = None
    map_url: str | None = None
    area_sqm: Decimal | None = None
    area_confidence: float = 0.0
    frontage_m: Decimal | None = None
    depth_m: Decimal | None = None
    price_type: str | None = None
    amount_lak: Decimal | None = None
    contact_hashes: frozenset[str] = frozenset()
    image_phashes: tuple[int, ...] = ()
    text: str | None = None
    post_date_ordinal: int | None = None     # days since epoch (date.toordinal())
    cluster_id: str | None = None


@dataclass
class Signal:
    name: str
    contribution: int
    evidence: str


@dataclass
class Score:
    total: int
    decision: str                           # HIGH_CONFIDENCE_MATCH | REVIEW_REQUIRED | SEPARATE_CANDIDATE
    signals: list[Signal] = field(default_factory=list)
    forced: str | None = None               # rule that forced the decision, if any
    match_version: str = MATCH_VERSION

    def to_dict(self) -> dict:
        return {"total": self.total, "decision": self.decision, "forced": self.forced, "match_version": self.match_version,
                "signals": [s.__dict__ for s in self.signals]}


def score_pair(a: Side, b: Side) -> Score:
    sig: list[Signal] = []
    forced: str | None = None

    def fire(name: str, evidence: str) -> None:
        sig.append(Signal(name, WEIGHTS[name], evidence))

    # ---- location ----------------------------------------------------------------
    dist = _distance_m(a, b)
    if dist is not None and PRECISION_RANK[a.precision] >= 6 and PRECISION_RANK[b.precision] >= 6 and dist <= 5:
        fire("coord_exact", f"{dist:.1f} m {HAVERSINE_TAG}")
    elif dist is not None and PRECISION_RANK[a.precision] >= 5 and PRECISION_RANK[b.precision] >= 5 and dist <= 30:
        fire("coord_near", f"{dist:.1f} m {HAVERSINE_TAG}")
    if a.map_url and b.map_url and a.map_url == b.map_url:
        fire("map_same", "identical map url")
    if a.village_code and b.village_code and PRECISION_RANK[a.precision] >= 4 and PRECISION_RANK[b.precision] >= 4:
        if a.village_code == b.village_code:
            fire("village_same", a.village_code)
    if a.district_code and b.district_code and PRECISION_RANK[a.precision] >= 3 and PRECISION_RANK[b.precision] >= 3:
        if a.district_code != b.district_code:
            fire("location_conflict", f"{a.district_code} vs {b.district_code}")
            forced = "location_conflict"
        elif not any(s.name == "village_same" for s in sig):
            fire("district_same", a.district_code)

    # ---- area / dimensions ---------------------------------------------------------
    if a.area_sqm and b.area_sqm and a.area_confidence >= 0.6 and b.area_confidence >= 0.6 and a.area_sqm > 0 and b.area_sqm > 0:
        delta = abs(a.area_sqm - b.area_sqm) / max(a.area_sqm, b.area_sqm)
        if delta <= Decimal("0.02"):
            fire("area_match", f"Δ {float(delta):.3%}")
        elif delta <= Decimal("0.10"):
            fire("area_close", f"Δ {float(delta):.1%}")
        elif delta > Decimal("0.40"):
            fire("area_conflict", f"Δ {float(delta):.0%}")
    if a.frontage_m and b.frontage_m and a.depth_m and b.depth_m:
        if abs(a.frontage_m - b.frontage_m) <= 1 and abs(a.depth_m - b.depth_m) <= 1:
            fire("frontage_depth_match", f"{a.frontage_m}x{a.depth_m} ~ {b.frontage_m}x{b.depth_m}")

    # ---- contacts / images / text ----------------------------------------------------
    shared = a.contact_hashes & b.contact_hashes
    if shared:
        fire("contact_same", f"{len(shared)} shared hash(es)")
    best_img = _best_phash_distance(a.image_phashes, b.image_phashes)
    if best_img is not None and best_img <= 6:
        fire("image_match", f"phash hamming {best_img}")
    if a.text and b.text:
        j = jaccard_3gram(a.text, b.text)
        if j >= 0.5:
            fire("text_similar", f"jaccard3 {j:.2f}")
        else:
            h = hamming(simhash64(a.text), simhash64(b.text))
            if h <= 3:
                fire("text_similar", f"simhash hamming {h}")

    # ---- price / type / time -------------------------------------------------------------
    if a.amount_lak and b.amount_lak and a.price_type and a.price_type == b.price_type and a.amount_lak > 0 and b.amount_lak > 0:
        d = abs(a.amount_lak - b.amount_lak) / max(a.amount_lak, b.amount_lak)
        if d <= Decimal("0.05"):
            fire("price_close", f"Δ {float(d):.1%}")
    if a.asset_type != "UNKNOWN" and b.asset_type != "UNKNOWN":
        if a.asset_type == b.asset_type:
            fire("asset_type_same", a.asset_type)
        elif a.asset_confidence >= 0.7 and b.asset_confidence >= 0.7:
            fire("asset_type_conflict", f"{a.asset_type} vs {b.asset_type}")
    if {a.signal_class, b.signal_class} == {"PROPERTY_SALE", "PROPERTY_RENT"}:
        fire("transaction_conflict", "SALE vs RENT")
    if a.post_date_ordinal is not None and b.post_date_ordinal is not None and abs(a.post_date_ordinal - b.post_date_ordinal) <= 180:
        fire("temporal_window", f"{abs(a.post_date_ordinal - b.post_date_ordinal)} d")
    if a.cluster_id and b.cluster_id and a.cluster_id == b.cluster_id:
        fire("same_cluster", a.cluster_id)

    total = max(0, min(100, sum(s.contribution for s in sig)))
    if forced == "location_conflict":
        decision = "SEPARATE_CANDIDATE"
    elif a.cluster_id and b.cluster_id and a.cluster_id == b.cluster_id:
        decision, forced = "HIGH_CONFIDENCE_MATCH", "same_cluster"  # cluster members always resolve together (001E §6)
    else:
        decision = "HIGH_CONFIDENCE_MATCH" if total >= HIGH else "REVIEW_REQUIRED" if total >= REVIEW else "SEPARATE_CANDIDATE"
        # contact alone never exceeds REVIEW (agents sell many properties)
        positive = [s for s in sig if s.contribution > 0]
        if decision == "HIGH_CONFIDENCE_MATCH" and all(s.name in ("contact_same", "temporal_window", "asset_type_same", "same_cluster") for s in positive):
            decision, forced = "REVIEW_REQUIRED", "contact_only_cap"
    return Score(total, decision, sig, forced)


# ---------------------------------------------------------------- helpers

def _distance_m(a: Side, b: Side) -> float | None:
    if a.lat is None or a.lng is None or b.lat is None or b.lng is None:
        return None
    r = 6_371_000.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dphi, dl = math.radians(b.lat - a.lat), math.radians(b.lng - a.lng)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _best_phash_distance(xs: tuple[int, ...], ys: tuple[int, ...]) -> int | None:
    if not xs or not ys:
        return None
    return min(hamming(x, y) for x in xs for y in ys)
