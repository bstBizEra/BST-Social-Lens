"""001E pure building blocks: canonical permalinks (§3), Lao-safe text similarity (§4/§6), MATCH_V1 scorer (§6)."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.resolution import (
    MATCH_VERSION, PERMALINK_RULES_VERSION, Side, canonical_permalink, hamming, jaccard_3gram, post_identity, score_pair, simhash64,
)

FIX = Path(__file__).parent / "fixtures" / "resolution" / "permalink-variants.json"
VARIANTS = json.loads(FIX.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- permalinks

@pytest.mark.parametrize("group", VARIANTS["groups"], ids=[g["name"] for g in VARIANTS["groups"]])
def test_variant_group_canonicalises_to_one_identity(group):
    canon = {canonical_permalink(u) for u in group["urls"]}
    assert len(canon) == 1, canon
    ids = {post_identity(u) for u in group["urls"]}
    expected = tuple(group["identity"]) if group["identity"] else None
    assert ids == {expected}


def test_distinct_posts_stay_distinct():
    canon = [canonical_permalink(u) for u in VARIANTS["distinct"]]
    assert len(set(canon)) == len(canon)


def test_unknown_shapes_are_normalised_not_destroyed():
    c = canonical_permalink("HTTP://M.FACEBOOK.COM/some/odd/path//x?b=2&a=1&fbclid=zz#frag")
    assert c == "https://www.facebook.com/some/odd/path/x/?a=1&b=2"
    assert canonical_permalink("not a url") == "not a url"
    assert canonical_permalink("") == ""
    assert PERMALINK_RULES_VERSION


# ---------------------------------------------------------------- text similarity

LAO_A = "ຂາຍດິນ 20x30 ບ້ານນາສ້າງໄຜ່ ເມືອງໄຊທານີ ລາຄາ 2.5 ຕື້ ໂທ 020 5512 3456"
LAO_A_REPOST = "ຂາຍດິນ 20x30 ບ້ານນາສ້າງໄຜ່ ເມືອງໄຊທານີ ລາຄາ 2.5 ຕື້ ໂທ 020 9988 7766 https://fb.me/x"
LAO_B = "ໃຫ້ເຊົ່າເຮືອນ 3 ຫ້ອງນອນ ບ້ານໂພນຕ້ອງ ລາຄາ 8 ລ້ານ/ເດືອນ"


def test_jaccard_ignores_contacts_urls_and_spacing():
    assert jaccard_3gram(LAO_A, LAO_A_REPOST) >= 0.9
    assert jaccard_3gram(LAO_A, "ຂາຍດິນ20x30ບ້ານນາສ້າງໄຜ່ ເມືອງໄຊທານີ ລາຄາ2.5ຕື້") >= 0.9
    assert jaccard_3gram(LAO_A, LAO_B) < 0.3
    assert jaccard_3gram("", LAO_A) == 0.0


def test_simhash_is_deterministic_and_near_for_reposts():
    assert simhash64(LAO_A) == simhash64(LAO_A)
    assert hamming(simhash64(LAO_A), simhash64(LAO_A_REPOST)) <= 3
    assert hamming(simhash64(LAO_A), simhash64(LAO_B)) > 10
    assert simhash64("") == 0


# ---------------------------------------------------------------- MATCH_V1

def side(**kw) -> Side:
    base = dict(observation_id=1, signal_class="PROPERTY_SALE", asset_type="LAND", asset_confidence=0.8, precision="VILLAGE",
                village_code="V-XTN-NSP", district_code="D-VTE-XTN", post_date_ordinal=date(2026, 9, 10).toordinal())
    base.update(kw)
    return Side(**base)


def total_of(s, *names):
    return sum(x.contribution for x in s.signals if x.name in names)


def test_multi_agent_same_land_high_confidence():
    a = side(lat=18.0600, lng=102.7000, precision="EXACT_COORDINATE", area_sqm=Decimal("600"), area_confidence=0.85,
             frontage_m=Decimal(20), depth_m=Decimal(30), price_type="ASKING_SALE", amount_lak=Decimal("2500000000"), text=LAO_A)
    b = side(observation_id=2, lat=18.06002, lng=102.70001, precision="EXACT_COORDINATE", area_sqm=Decimal("600"), area_confidence=0.9,
             frontage_m=Decimal(20), depth_m=Decimal(30), price_type="ASKING_SALE", amount_lak=Decimal("2700000000"),
             text="ດິນງາມ 20x30 ນາສ້າງໄຜ່ ໄຊທານີ ຕິດຕໍ່ນາຍໜ້າ ລາຄາ 2.7 ຕື້")
    s = score_pair(a, b)
    names = {x.name for x in s.signals}
    assert {"coord_exact", "area_match", "frontage_depth_match", "village_same", "asset_type_same", "temporal_window"} <= names
    # CALIBRATION CANDIDATE (001E §11.1 / E1): with the uncalibrated starting weights a shared 5 m pin + identical
    # 20x30 dimensions + same village sums to 66 → REVIEW_REQUIRED. The reviewed sample decides whether coord_exact
    # should carry more weight. Recorded here so the behaviour is explicit, not accidental.
    assert s.total == 66 and s.decision == "REVIEW_REQUIRED" and s.forced is None
    # the same pair with an identical map link crosses the line
    a.map_url = b.map_url = "https://www.google.com/maps/place/x/@18.06,102.7,17z"
    s2 = score_pair(a, b)
    assert s2.total == 91 and s2.decision == "HIGH_CONFIDENCE_MATCH"
    assert "price_close" not in names  # 8 % apart — different agents, different asks (kept as evidence, not penalised)
    assert any("approx:haversine" in x.evidence for x in s.signals)  # E6 tag
    assert sum(x.contribution for x in s.signals) == s.total or s.total == 100
    assert s.match_version == MATCH_VERSION and "uncalibrated" in MATCH_VERSION


def test_repost_same_cluster_and_text():
    a = side(text=LAO_A, contact_hashes=frozenset({"h1"}), cluster_id="C1", area_sqm=Decimal("600"), area_confidence=0.85)
    b = side(observation_id=2, text=LAO_A_REPOST, contact_hashes=frozenset({"h1"}), cluster_id="C1", area_sqm=Decimal("600"), area_confidence=0.85)
    s = score_pair(a, b)
    assert {"text_similar", "contact_same", "same_cluster", "area_match", "village_same"} <= {x.name for x in s.signals}
    assert s.decision == "HIGH_CONFIDENCE_MATCH" and s.forced == "same_cluster"  # cluster members always resolve together


def test_location_conflict_forces_separate_even_with_high_sum():
    a = side(district_code="D-VTE-XTN", village_code=None, area_sqm=Decimal("600"), area_confidence=0.9, contact_hashes=frozenset({"h"}), text=LAO_A)
    b = side(observation_id=2, district_code="D-CPS-PKS", village_code=None, area_sqm=Decimal("600"), area_confidence=0.9, contact_hashes=frozenset({"h"}), text=LAO_A)
    s = score_pair(a, b)
    assert s.decision == "SEPARATE_CANDIDATE" and s.forced == "location_conflict"
    assert total_of(s, "location_conflict") == -40


def test_contact_alone_caps_at_review():
    a = side(precision="TEXT_ONLY", village_code=None, district_code=None, contact_hashes=frozenset({"h", "h2"}))
    b = side(observation_id=2, precision="TEXT_ONLY", village_code=None, district_code=None, contact_hashes=frozenset({"h", "h2"}))
    s = score_pair(a, b)
    assert s.total < 80 and s.decision != "HIGH_CONFIDENCE_MATCH"
    assert {x.name for x in s.signals if x.contribution > 0} <= {"contact_same", "asset_type_same", "temporal_window"}
    # the cap is a rule, not an accident of the weights: force the sum over 80 with only capped signals and check
    from app.resolution import match as m
    saved = m.WEIGHTS["contact_same"]
    m.WEIGHTS["contact_same"] = 90
    try:
        s2 = score_pair(a, b)
    finally:
        m.WEIGHTS["contact_same"] = saved
    assert s2.total >= 80 and s2.decision == "REVIEW_REQUIRED" and s2.forced == "contact_only_cap"


def test_area_and_asset_conflicts_penalise():
    a = side(area_sqm=Decimal("600"), area_confidence=0.9, asset_type="LAND", asset_confidence=0.8)
    b = side(observation_id=2, area_sqm=Decimal("1500"), area_confidence=0.9, asset_type="WAREHOUSE", asset_confidence=0.85)
    s = score_pair(a, b)
    assert total_of(s, "area_conflict") == -20 and total_of(s, "asset_type_conflict") == -25
    assert s.decision == "SEPARATE_CANDIDATE" and s.total == 0  # clamped at 0


def test_sale_vs_rent_conflict_and_precision_gates():
    a = side(signal_class="PROPERTY_SALE", lat=18.06, lng=102.70, precision="VILLAGE")
    b = side(observation_id=2, signal_class="PROPERTY_RENT", lat=18.06, lng=102.70, precision="VILLAGE")
    s = score_pair(a, b)
    names = {x.name for x in s.signals}
    assert "transaction_conflict" in names
    assert "coord_exact" not in names and "coord_near" not in names  # VILLAGE centroids never count as coordinates (I6)


def test_image_phash_match():
    a = side(image_phashes=(0b1010_1010_1111_0000,), village_code=None, district_code=None, precision="TEXT_ONLY")
    b = side(observation_id=2, image_phashes=(0b1010_1010_1111_0011, 0b1), village_code=None, district_code=None, precision="TEXT_ONLY")
    s = score_pair(a, b)
    assert total_of(s, "image_match") == 15


def test_score_is_symmetric_and_explainable():
    a = side(lat=18.0600, lng=102.7000, precision="EXACT_COORDINATE", area_sqm=Decimal("600"), area_confidence=0.85, text=LAO_A)
    b = side(observation_id=2, lat=18.0601, lng=102.7001, precision="PARCEL_APPROXIMATE", area_sqm=Decimal("650"), area_confidence=0.85, text=LAO_A_REPOST)
    s1, s2 = score_pair(a, b), score_pair(b, a)
    assert s1.total == s2.total and s1.decision == s2.decision
    assert sorted(x.name for x in s1.signals) == sorted(x.name for x in s2.signals)
    d = s1.to_dict()
    assert d["total"] == sum(x["contribution"] for x in d["signals"]) or d["total"] in (0, 100)
