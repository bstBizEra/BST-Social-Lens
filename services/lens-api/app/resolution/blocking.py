"""Candidate generation by blocking (001E §5) — pure: Sides in, candidate index pairs out.

Rules (any one blocks a pair): same village_code at precision ≥ VILLAGE; points within 300 m at
precision ≥ PARCEL_APPROXIMATE; shared contact hash; same listing cluster. Observations that are
TEXT_ONLY/UNKNOWN without a contact hash are never blocked (I6) and are reported as `unlocatable`.
Only PROPERTY_SALE / PROPERTY_RENT with a known asset type enter (E3).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .match import PRECISION_RANK, Side, _distance_m

BLOCKING_VERSION = "1.0.0"
NEAR_M = 300.0
ELIGIBLE_CLASSES = {"PROPERTY_SALE", "PROPERTY_RENT"}


@dataclass
class BlockingResult:
    pairs: list[tuple[int, int]]                       # index pairs (i < j) into the input list
    reasons: dict[tuple[int, int], list[str]] = field(default_factory=dict)
    eligible: list[int] = field(default_factory=list)
    ineligible: dict[int, str] = field(default_factory=dict)   # index → reason
    unlocatable: list[int] = field(default_factory=list)
    blocking_version: str = BLOCKING_VERSION


def block(sides: list[Side]) -> BlockingResult:
    res = BlockingResult(pairs=[])
    for i, s in enumerate(sides):
        if s.signal_class not in ELIGIBLE_CLASSES:
            res.ineligible[i] = f"class:{s.signal_class}"
        elif s.asset_type == "UNKNOWN":
            res.ineligible[i] = "asset:UNKNOWN"
        else:
            res.eligible.append(i)
    reasons: dict[tuple[int, int], list[str]] = defaultdict(list)

    by_village: dict[str, list[int]] = defaultdict(list)
    by_contact: dict[str, list[int]] = defaultdict(list)
    by_cluster: dict[str, list[int]] = defaultdict(list)
    pointed: list[int] = []
    for i in res.eligible:
        s = sides[i]
        locatable = PRECISION_RANK[s.precision] >= 2 and (s.village_code or s.district_code or s.lat is not None)
        if s.village_code and PRECISION_RANK[s.precision] >= 4:
            by_village[s.village_code].append(i)
        if PRECISION_RANK[s.precision] >= 5 and s.lat is not None and s.lng is not None:
            pointed.append(i)
        for h in s.contact_hashes:
            by_contact[h].append(i)
        if s.cluster_id:
            by_cluster[s.cluster_id].append(i)
        if not locatable and not s.contact_hashes:
            res.unlocatable.append(i)

    def add(i: int, j: int, why: str) -> None:
        if i == j:
            return
        key = (min(i, j), max(i, j))
        if why not in reasons[key]:
            reasons[key].append(why)

    for code, idx in by_village.items():
        for a in range(len(idx)):
            for b in range(a + 1, len(idx)):
                add(idx[a], idx[b], f"village:{code}")
    for a in range(len(pointed)):
        for b in range(a + 1, len(pointed)):
            i, j = pointed[a], pointed[b]
            d = _distance_m(sides[i], sides[j])
            if d is not None and d <= NEAR_M:
                add(i, j, f"near:{d:.0f}m approx:haversine")
    for h, idx in by_contact.items():
        for a in range(len(idx)):
            for b in range(a + 1, len(idx)):
                add(idx[a], idx[b], f"contact:{h[:8]}")
    for c, idx in by_cluster.items():
        for a in range(len(idx)):
            for b in range(a + 1, len(idx)):
                add(idx[a], idx[b], f"cluster:{c}")

    res.pairs = sorted(reasons)
    res.reasons = dict(reasons)
    return res
