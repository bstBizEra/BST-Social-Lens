"""001E §4 listing clusters and §5 blocking — pure, deterministic, evidence-preserving."""
from __future__ import annotations

from decimal import Decimal

from app.resolution import ClusterInput, Side, block, build_clusters, pair_evidence

TXT = "ຂາຍດິນ 20x30 ບ້ານນາສ້າງໄຜ່ ເມືອງໄຊທານີ ລາຄາ 2.5 ຕື້ ໂທ 020 5512 3456"
TXT_REPOST = "ຂາຍດິນ 20x30 ບ້ານນາສ້າງໄຜ່ ເມືອງໄຊທານີ ລາຄາ 2.5 ຕື້ ໂທ 020 9988 7766"
TXT_OTHER = "ໃຫ້ເຊົ່າເຮືອນ 3 ຫ້ອງນອນ ບ້ານໂພນຕ້ອງ ລາຄາ 8 ລ້ານ/ເດືອນ"


def ci(key, **kw):
    return ClusterInput(record_key=key, **kw)


# ---------------------------------------------------------------- clusters

def test_exact_text_repost_clusters_alone():
    a, b = ci("facebook:1", text=TXT), ci("facebook:2", text=TXT_REPOST)
    ev = pair_evidence(a, b)
    assert ev.accepted and ev.rule == "exact" and "text_simhash" in ev.signals


def test_one_strong_signal_is_not_enough():
    a = ci("facebook:1", contact_hashes=frozenset({"h"}), text=TXT)
    b = ci("facebook:2", contact_hashes=frozenset({"h"}), text=TXT_OTHER)
    ev = pair_evidence(a, b)
    assert not ev.accepted and set(ev.signals) == {"contact_hash"}  # an agent posts many listings


def test_two_strong_or_strong_plus_weak():
    a = ci("facebook:1", contact_hashes=frozenset({"h"}), image_phashes=(0xABCD,), text=TXT)
    b = ci("facebook:2", contact_hashes=frozenset({"h"}), image_phashes=(0xABCF,), text=TXT_OTHER)
    assert pair_evidence(a, b).rule == "two_strong"
    c = ci("facebook:3", contact_hashes=frozenset({"h"}), amount_original=Decimal("2500000000"), currency="LAK", text=TXT_OTHER)
    d = ci("facebook:4", contact_hashes=frozenset({"h"}), amount_original=Decimal("2500000000"), currency="LAK", text="ດິນຕິດຖະໜົນ ຂາຍດ່ວນ")
    assert pair_evidence(c, d).rule == "strong_plus_weak"


def test_weak_signals_alone_never_cluster():
    a = ci("facebook:1", amount_original=Decimal("1"), currency="LAK", village_code="V1", author_hash="au", text=TXT)
    b = ci("facebook:2", amount_original=Decimal("1"), currency="LAK", village_code="V1", author_hash="au", text=TXT_OTHER)
    ev = pair_evidence(a, b)
    assert not ev.accepted and set(ev.signals) == {"price_exact", "location_same", "author_same"}


def test_components_are_transitive_and_ids_stable():
    items = [
        ci("facebook:9", text=TXT),                                        # exact text with :2
        ci("facebook:2", text=TXT_REPOST, contact_hashes=frozenset({"h"}), image_phashes=(0x10,)),
        ci("facebook:5", text=TXT_OTHER, contact_hashes=frozenset({"h"}), image_phashes=(0x11,)),  # two strong with :2
        ci("facebook:7", text="ບໍ່ກ່ຽວ"),                                   # singleton
    ]
    clusters, evidence = build_clusters(items)
    assert len(clusters) == 1
    (c,) = clusters
    assert c.cluster_id == "C-facebook:2" and c.members == ["facebook:2", "facebook:5", "facebook:9"]
    assert {e.rule for e in c.edges} == {"exact", "two_strong"}
    assert len(evidence) == 6  # all pairs evaluated, evidence kept for the rejected ones too
    # order-independent
    clusters2, _ = build_clusters(list(reversed(items)))
    assert [(x.cluster_id, x.members) for x in clusters2] == [(c.cluster_id, c.members)]


def test_candidate_pairs_restrict_evaluation():
    items = [ci("facebook:1", text=TXT), ci("facebook:2", text=TXT_REPOST), ci("facebook:3", text=TXT)]
    clusters, evidence = build_clusters(items, candidate_pairs=[(0, 1)])
    assert len(evidence) == 1 and clusters[0].members == ["facebook:1", "facebook:2"]


def test_location_same_by_point_only_at_point_precision():
    a = ci("facebook:1", contact_hashes=frozenset({"h"}), precision="EXACT_COORDINATE", lat=18.06, lng=102.70, text=TXT)
    b = ci("facebook:2", contact_hashes=frozenset({"h"}), precision="EXACT_COORDINATE", lat=18.0605, lng=102.7005, text=TXT_OTHER)
    assert pair_evidence(a, b).rule == "strong_plus_weak"
    b.precision = "VILLAGE"
    assert not pair_evidence(a, b).accepted


# ---------------------------------------------------------------- blocking

def side(i, **kw):
    base = dict(observation_id=i, signal_class="PROPERTY_SALE", asset_type="LAND", precision="VILLAGE")
    base.update(kw)
    return Side(**base)


def test_blocking_rules_and_reasons():
    sides = [
        side(0, village_code="V1"),
        side(1, village_code="V1"),
        side(2, village_code="V2", precision="EXACT_COORDINATE", lat=18.0600, lng=102.7000),
        side(3, village_code="V3", precision="PARCEL_APPROXIMATE", lat=18.0610, lng=102.7010),   # ~155 m from 2
        side(4, precision="TEXT_ONLY", village_code=None, contact_hashes=frozenset({"h"})),
        side(5, precision="TEXT_ONLY", village_code=None, contact_hashes=frozenset({"h"})),
        side(6, precision="TEXT_ONLY", village_code=None),                                        # unlocatable
        side(7, signal_class="PROPERTY_WANTED", village_code="V1"),                               # E3
        side(8, asset_type="UNKNOWN", village_code="V1"),
        side(9, village_code="V9", cluster_id="C-x"), side(10, village_code="V10", cluster_id="C-x"),
    ]
    r = block(sides)
    assert (0, 1) in r.pairs and r.reasons[(0, 1)] == ["village:V1"]
    assert (2, 3) in r.pairs and r.reasons[(2, 3)][0].startswith("near:") and "approx:haversine" in r.reasons[(2, 3)][0]
    assert (4, 5) in r.pairs and r.reasons[(4, 5)] == ["contact:h"]
    assert (9, 10) in r.pairs and r.reasons[(9, 10)] == ["cluster:C-x"]
    assert r.unlocatable == [6] and not any(6 in p for p in r.pairs)
    assert r.ineligible == {7: "class:PROPERTY_WANTED", 8: "asset:UNKNOWN"} and not any(7 in p or 8 in p for p in r.pairs)
    assert r.eligible == [0, 1, 2, 3, 4, 5, 6, 9, 10]


def test_village_precision_gate_and_no_all_pairs():
    sides = [side(0, village_code="V1", precision="DISTRICT"), side(1, village_code="V1", precision="DISTRICT"), side(2, village_code="V1")]
    r = block(sides)
    assert r.pairs == []  # village match requires precision ≥ VILLAGE on both sides


def test_blocking_is_deterministic():
    sides = [side(i, village_code="V1") for i in range(5)]
    assert block(sides).pairs == block(sides).pairs == [(0, 1), (0, 2), (0, 3), (0, 4), (1, 2), (1, 3), (1, 4), (2, 3), (2, 4), (3, 4)]
