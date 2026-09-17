"""Listing clusters (001E §4) — pure: records in, connected components out.

A pair is accepted when ONE exact signal or TWO independent strong signals agree:
  text_simhash   exact when Hamming ≤ 1, strong when ≤ 3
  image_phash    strong when any image pair Hamming ≤ 6
  contact_hash   strong when a contact hash is shared
  price_exact / location_same / author_same are weak — they never form a pair alone or together,
  but one weak signal may complete a pair that has exactly one strong signal (two independent
  signals in total). Clusters are connected components; a record belongs to one cluster.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

from .textsim import hamming, simhash64

CLUSTER_VERSION = "1.0.0"
STRONG = {"text_simhash", "image_phash", "contact_hash"}
WEAK = {"price_exact", "location_same", "author_same"}
LOCATION_M = 200.0
POINT_PRECISIONS = {"EXACT_COORDINATE", "PARCEL_APPROXIMATE"}


@dataclass
class ClusterInput:
    """What clustering needs from one source record / its current observation."""
    record_key: str
    text: str | None = None
    simhash: int | None = None            # precomputed simhash64(text); computed if None
    image_phashes: tuple[int, ...] = ()
    contact_hashes: frozenset[str] = frozenset()
    author_hash: str | None = None
    amount_original: Decimal | None = None
    currency: str | None = None
    village_code: str | None = None
    precision: str = "UNKNOWN"
    lat: float | None = None
    lng: float | None = None

    def __post_init__(self) -> None:
        if self.simhash is None:
            self.simhash = simhash64(self.text) if self.text else None


@dataclass
class PairEvidence:
    a: str
    b: str
    signals: dict[str, str] = field(default_factory=dict)   # name → evidence
    accepted: bool = False
    rule: str | None = None                                  # "exact" | "two_strong" | "strong_plus_weak"


@dataclass
class Cluster:
    cluster_id: str            # deterministic: "C-" + min record_key in the component (stable across runs)
    members: list[str]
    edges: list[PairEvidence]
    cluster_version: str = CLUSTER_VERSION


def pair_evidence(x: ClusterInput, y: ClusterInput) -> PairEvidence:
    ev = PairEvidence(x.record_key, y.record_key)
    exact = False
    if x.simhash and y.simhash:
        h = hamming(x.simhash, y.simhash)
        if h <= 3:
            ev.signals["text_simhash"] = f"hamming {h}"
            exact = exact or h <= 1
    if x.image_phashes and y.image_phashes:
        best = min(hamming(p, q) for p in x.image_phashes for q in y.image_phashes)
        if best <= 6:
            ev.signals["image_phash"] = f"hamming {best}"
    shared = x.contact_hashes & y.contact_hashes
    if shared:
        ev.signals["contact_hash"] = f"{len(shared)} shared"
    if x.amount_original and y.amount_original and x.currency and x.currency == y.currency and x.amount_original == y.amount_original:
        ev.signals["price_exact"] = f"{x.amount_original} {x.currency}"
    if x.village_code and y.village_code and x.village_code == y.village_code:
        ev.signals["location_same"] = f"village {x.village_code}"
    elif x.precision in POINT_PRECISIONS and y.precision in POINT_PRECISIONS and None not in (x.lat, x.lng, y.lat, y.lng):
        d = _haversine_m(x.lat, x.lng, y.lat, y.lng)  # type: ignore[arg-type]
        if d <= LOCATION_M:
            ev.signals["location_same"] = f"{d:.0f} m approx:haversine"
    if x.author_hash and y.author_hash and x.author_hash == y.author_hash:
        ev.signals["author_same"] = x.author_hash[:8]

    strong = [s for s in ev.signals if s in STRONG]
    weak = [s for s in ev.signals if s in WEAK]
    if exact:
        ev.accepted, ev.rule = True, "exact"
    elif len(strong) >= 2:
        ev.accepted, ev.rule = True, "two_strong"
    elif len(strong) == 1 and weak:
        ev.accepted, ev.rule = True, "strong_plus_weak"
    return ev


def build_clusters(items: list[ClusterInput], candidate_pairs: list[tuple[int, int]] | None = None) -> tuple[list[Cluster], list[PairEvidence]]:
    """Return (clusters with ≥ 2 members, all evaluated pair evidence). `candidate_pairs` restricts evaluation
    (blocking); None evaluates all pairs (fine for tests and small batches)."""
    n = len(items)
    pairs = candidate_pairs if candidate_pairs is not None else [(i, j) for i in range(n) for j in range(i + 1, n)]
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    evidence: list[PairEvidence] = []
    for i, j in pairs:
        ev = pair_evidence(items[i], items[j])
        evidence.append(ev)
        if ev.accepted:
            parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    clusters: list[Cluster] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        keys = sorted(items[i].record_key for i in members)
        edges = [e for e in evidence if e.accepted and e.a in keys and e.b in keys]
        clusters.append(Cluster("C-" + keys[0], keys, edges))
    clusters.sort(key=lambda c: c.cluster_id)
    return clusters, evidence


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))
