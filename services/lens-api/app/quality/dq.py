"""DQ score / grade and validation rules (001G §2). Pure, deterministic, versioned.

    score_observation(DqInput) -> DqResult(score, grade, components{name: (points, max, reason)})
    property_dq([(score, weight)])  -> (score, grade)   # observation-count-weighted mean
    evaluate_rules(obs_view)        -> [Exception(rule, kind, detail)]

DQ is a *coverage* score: it never says a value is true, only how complete and traceable the
observation is. It is kept separate from resolution confidence (001E) by construction — no
input here is a match score.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

DQ_VERSION = "1.0.0"
WEIGHTS = {"price": 20, "area": 15, "location": 20, "coordinate": 15, "post_date": 10, "evidence": 10, "entity_match": 10}
GRADES = (("A", 90), ("B", 75), ("C", 60), ("D", 0))

_LOCATION_POINTS = {"EXACT_COORDINATE": 20, "PARCEL_APPROXIMATE": 20, "VILLAGE": 20, "DISTRICT": 12, "PROVINCE": 6, "TEXT_ONLY": 0, "UNKNOWN": 0}
_COORD_POINTS = {"EXACT_COORDINATE": 15, "PARCEL_APPROXIMATE": 10}
_MATCH_POINTS = {"CONFIRMED": 10, "HIGH_CONFIDENCE_MATCH": 10, "REVIEW_REQUIRED": 5, "SEPARATE_CANDIDATE": 5, "SINGLETON": 5, "REJECTED": 0, "UNLINKED": 0}


@dataclass
class DqInput:
    """Everything §2 needs about one observation. Absent fields score zero for their component."""
    price_present: bool = False
    price_confidence: float = 0.0
    price_has_lak: bool = False                 # amount_lak resolved (FX or identity)
    area_present: bool = False
    area_confidence: float = 0.0
    location_precision: str = "UNKNOWN"         # primary resolution precision (001D)
    post_date_present: bool = False
    payload_hash_present: bool = False          # first_payload_hash on the L1 row
    raw_body_present: bool = False              # raw capture body still stored
    raw_row_present: bool = False               # raw capture row exists (hash + context)
    decision: str | None = None                 # current entity decision (001E) or None (no resolution yet)


@dataclass
class DqResult:
    score: int
    grade: str
    components: dict[str, tuple[int, int, str]] = field(default_factory=dict)   # name → (points, max, reason)
    dq_version: str = DQ_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {"score": self.score, "grade": self.grade, "dq_version": self.dq_version,
                "components": {k: {"points": p, "max": m, "reason": r} for k, (p, m, r) in self.components.items()}}


def grade(score: int | float) -> str:
    for g, floor in GRADES:
        if score >= floor:
            return g
    return "D"


def score_observation(x: DqInput) -> DqResult:
    c: dict[str, tuple[int, int, str]] = {}
    # price (20): full with LAK + conf ≥ 0.6; 10 if present but NO_FX; 0 otherwise
    if x.price_present and x.price_confidence >= 0.6 and x.price_has_lak:
        c["price"] = (20, 20, "amount_lak resolved, conf ≥ 0.6")
    elif x.price_present and x.price_confidence >= 0.6:
        c["price"] = (10, 20, "present, NO_FX")
    elif x.price_present:
        c["price"] = (0, 20, f"present but conf {x.price_confidence:.2f} < 0.6")
    else:
        c["price"] = (0, 20, "absent")
    # area (15): full conf ≥ 0.6; 8 if 0.3–0.6
    if x.area_present and x.area_confidence >= 0.6:
        c["area"] = (15, 15, "area_sqm, conf ≥ 0.6")
    elif x.area_present and x.area_confidence >= 0.3:
        c["area"] = (8, 15, f"area_sqm, conf {x.area_confidence:.2f}")
    else:
        c["area"] = (0, 15, "absent or conf < 0.3")
    # location (20) by precision
    lp = _LOCATION_POINTS.get(x.location_precision, 0)
    c["location"] = (lp, 20, f"precision {x.location_precision}")
    # coordinate (15)
    cp = _COORD_POINTS.get(x.location_precision, 0)
    c["coordinate"] = (cp, 15, "pin" if cp == 15 else "approximate" if cp else "no point")
    # post date (10)
    c["post_date"] = (10 if x.post_date_present else 0, 10, "present" if x.post_date_present else "absent")
    # evidence (10): 10 if hash resolves to a stored body; 5 if row/hash present but body purged; 0 without hash
    if x.payload_hash_present and x.raw_row_present and x.raw_body_present:
        c["evidence"] = (10, 10, "raw body stored")
    elif x.payload_hash_present:
        c["evidence"] = (5, 10, "hash present, body purged or row pending")
    else:
        c["evidence"] = (0, 10, "no payload hash")
    # entity match (10)
    if x.decision is None:
        c["entity_match"] = (5, 10, "not yet resolved (singleton)")
    else:
        c["entity_match"] = (_MATCH_POINTS.get(x.decision, 0), 10, f"decision {x.decision}")
    total = sum(p for p, _, _ in c.values())
    assert sum(m for _, m, _ in c.values()) == 100
    return DqResult(total, grade(total), c)


def property_dq(observation_scores: list[tuple[int, int]]) -> tuple[int, str]:
    """(score, weight) per current observation → observation-count-weighted mean, rounded, graded."""
    if not observation_scores:
        return 0, "D"
    w = sum(max(1, wt) for _, wt in observation_scores)
    s = round(sum(sc * max(1, wt) for sc, wt in observation_scores) / w)
    return s, grade(s)


# ---------------------------------------------------------------- validation rules (§2, exceptions never silent fixes)

@dataclass
class Exception_:
    rule: str
    kind: str          # price | area | date | entity | location | contact | integrity
    detail: str


RULES_VERSION = "1.0.0"
PRICE_MIN_LAK, PRICE_MAX_LAK = Decimal("1000000"), Decimal("100000000000")
AREA_MIN, AREA_MAX = Decimal("4"), Decimal("5000000")             # 500 ha
PSQM_MIN, PSQM_MAX = Decimal("10000"), Decimal("500000000")
DATE_MIN = date(2015, 1, 1)
CONTACT_SIGHTINGS_MAX = 200


def evaluate_rules(v: dict[str, Any], today: date | None = None) -> list[Exception_]:
    """`v` is a flat view: amount_lak, area_sqm, price_per_sqm_lak, post_date (date), has_price_claim, has_price_observation,
    has_location_claims, has_resolution, mp_current_observations (int|None), contact_sightings (int|None)."""
    out: list[Exception_] = []
    today = today or date.today()
    lak = v.get("amount_lak")
    if lak is not None and not (PRICE_MIN_LAK <= Decimal(str(lak)) <= PRICE_MAX_LAK):
        out.append(Exception_("price_range", "price", f"amount_lak {lak} outside {PRICE_MIN_LAK}–{PRICE_MAX_LAK}"))
    area = v.get("area_sqm")
    if area is not None and not (AREA_MIN <= Decimal(str(area)) <= AREA_MAX):
        out.append(Exception_("area_range", "area", f"area_sqm {area} outside {AREA_MIN}–{AREA_MAX}"))
    psqm = v.get("price_per_sqm_lak")
    if psqm is not None and not (PSQM_MIN <= Decimal(str(psqm)) <= PSQM_MAX):
        out.append(Exception_("per_sqm_range", "price", f"price_per_sqm_lak {psqm} outside {PSQM_MIN}–{PSQM_MAX}"))
    pd_ = v.get("post_date")
    if pd_ is not None and (pd_ > today or pd_ < DATE_MIN):
        out.append(Exception_("post_date_range", "date", f"post_date {pd_} in the future or before {DATE_MIN}"))
    if v.get("has_price_observation") and not v.get("has_price_claim"):
        out.append(Exception_("price_observation_without_claim", "integrity", "price observation has no PRICE claim"))
    if v.get("has_location_claims") and not v.get("has_resolution"):
        out.append(Exception_("location_claim_without_resolution", "location", "location claims present but no resolved_locations row"))
    mp = v.get("mp_current_observations")
    if mp is not None and mp == 0:
        out.append(Exception_("market_property_without_observations", "entity", "market property has zero current observations"))
    cs = v.get("contact_sightings")
    if cs is not None and cs > CONTACT_SIGHTINGS_MAX:
        out.append(Exception_("contact_sightings_anomaly", "contact", f"{cs} sightings > {CONTACT_SIGHTINGS_MAX}"))
    return out
