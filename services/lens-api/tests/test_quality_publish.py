"""001G DQ scorer + validation rules, 001H bundle negative check — pure, fixture-driven."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from app.publish import check_bundle_text, check_records
from app.quality import DQ_VERSION, DqInput, evaluate_rules, grade, property_dq, score_observation

FULL = DqInput(price_present=True, price_confidence=0.9, price_has_lak=True, area_present=True, area_confidence=0.85,
               location_precision="EXACT_COORDINATE", post_date_present=True, payload_hash_present=True, raw_row_present=True,
               raw_body_present=True, decision="CONFIRMED")


def test_full_observation_scores_100_grade_a():
    r = score_observation(FULL)
    assert r.score == 100 and r.grade == "A" and r.dq_version == DQ_VERSION
    assert sum(m for _, m, _ in r.components.values()) == 100
    d = r.to_dict()
    assert set(d["components"]) == {"price", "area", "location", "coordinate", "post_date", "evidence", "entity_match"}


def test_empty_observation_scores_singleton_only():
    r = score_observation(DqInput())
    assert r.score == 5 and r.grade == "D"  # only the not-yet-resolved singleton credit


def test_partial_credit_rules():
    r = score_observation(DqInput(price_present=True, price_confidence=0.9, price_has_lak=False))
    assert r.components["price"] == (10, 20, "present, NO_FX")
    r = score_observation(DqInput(area_present=True, area_confidence=0.45))
    assert r.components["area"][0] == 8
    r = score_observation(DqInput(location_precision="DISTRICT"))
    assert r.components["location"][0] == 12 and r.components["coordinate"][0] == 0
    r = score_observation(DqInput(location_precision="PARCEL_APPROXIMATE"))
    assert r.components["location"][0] == 20 and r.components["coordinate"][0] == 10
    r = score_observation(DqInput(payload_hash_present=True, raw_row_present=True, raw_body_present=False))
    assert r.components["evidence"][0] == 5
    r = score_observation(DqInput(decision="REJECTED"))
    assert r.components["entity_match"][0] == 0
    r = score_observation(DqInput(decision="REVIEW_REQUIRED"))
    assert r.components["entity_match"][0] == 5


def test_grade_boundaries_and_property_mean():
    assert [grade(x) for x in (100, 90, 89, 75, 74, 60, 59, 0)] == ["A", "A", "B", "B", "C", "C", "D", "D"]
    assert property_dq([]) == (0, "D")
    assert property_dq([(100, 1), (50, 1)]) == (75, "B")
    assert property_dq([(100, 3), (40, 1)]) == (85, "B")


def test_dq_is_independent_of_match_score():
    """A complete listing can be linked to the wrong property: DQ takes only the decision *state*, never a score."""
    a = score_observation(FULL)
    b = score_observation(DqInput(**{**FULL.__dict__, "decision": "HIGH_CONFIDENCE_MATCH"}))
    assert a.score == b.score
    assert "score" not in DqInput.__dataclass_fields__ and "confidence_match" not in DqInput.__dataclass_fields__


def test_determinism():
    assert json.dumps(score_observation(FULL).to_dict(), sort_keys=True) == json.dumps(score_observation(FULL).to_dict(), sort_keys=True)


# ---------------------------------------------------------------- rules

def test_validation_rules_raise_exceptions_never_fix():
    today = date(2026, 9, 17)
    v = {"amount_lak": Decimal("500000"), "area_sqm": Decimal("2"), "price_per_sqm_lak": Decimal("999999999"),
         "post_date": date(2027, 1, 1), "has_price_observation": True, "has_price_claim": False,
         "has_location_claims": True, "has_resolution": False, "mp_current_observations": 0, "contact_sightings": 250}
    ex = evaluate_rules(v, today)
    assert {e.rule for e in ex} == {"price_range", "area_range", "per_sqm_range", "post_date_range", "price_observation_without_claim",
                                    "location_claim_without_resolution", "market_property_without_observations", "contact_sightings_anomaly"}
    assert {e.kind for e in ex} == {"price", "area", "date", "integrity", "location", "entity", "contact"}
    assert v["amount_lak"] == Decimal("500000")  # nothing mutated


def test_clean_view_has_no_exceptions():
    v = {"amount_lak": Decimal("2500000000"), "area_sqm": Decimal("600"), "price_per_sqm_lak": Decimal("4166667"),
         "post_date": date(2026, 9, 10), "has_price_observation": True, "has_price_claim": True,
         "has_location_claims": True, "has_resolution": True, "mp_current_observations": 3, "contact_sightings": 4}
    assert evaluate_rules(v, date(2026, 9, 17)) == []
    assert evaluate_rules({}, date(2026, 9, 17)) == []  # absent fields raise nothing


# ---------------------------------------------------------------- bundle negative check (001H §4)

CLEAN_BUNDLE = {
    "dataset_version": {"dataset_id": "lao-residential-market", "version": "2026.11.1", "checksum": "a" * 64},
    "market_properties": [{"market_property_id": "MP-01ARZ3NDEKTSV4RRFFQ69G5FAV", "asset_type": "LAND", "dq_grade": "B",
                           "location": {"district_code": "D-VTE-XTN", "precision": "VILLAGE"},
                           "statistics": {"ASKING_SALE": {"median_lak": "2500000000", "n": 3}}}],
    "observations": [{"observation_id": 37, "market_property_id": "MP-01ARZ3NDEKTSV4RRFFQ69G5FAV", "platform": "facebook",
                      "post_date": "2026-09-10", "advertiser_role": "FREELANCE_AGENT", "first_payload_hash": "b" * 64,
                      "price_observations": [{"price_type": "ASKING_SALE", "amount_lak": "2500000000", "evidence_level": "D", "price_basis": "OBSERVED_ASKING"}]}],
}


def test_clean_bundle_passes():
    assert check_records(CLEAN_BUNDLE) == []
    assert check_bundle_text(json.dumps(CLEAN_BUNDLE, ensure_ascii=False)) == []


def test_bundle_check_catches_every_leak_kind():
    dirty = json.loads(json.dumps(CLEAN_BUNDLE))
    dirty["observations"][0]["text"] = "ຂາຍດິນ ໂທ 020 5512 3456"
    dirty["observations"][0]["author_hash"] = "c" * 64
    dirty["observations"][0]["note"] = "LINE id: vily_prop or 02099887766 or https://www.facebook.com/groups/1/posts/2/"
    dirty["market_properties"][0]["contact"] = "d" * 64
    dirty["market_properties"][0]["mail"] = "agent@example.la"
    kinds = {v.kind for v in check_records(dirty)}
    assert {"forbidden_key", "phone", "messaging_handle", "platform_url", "email", "unexpected_hash"} <= kinds
    assert any(v.sample.startswith("observations[0].text") for v in check_records(dirty))  # forbidden key located
    t = check_bundle_text(json.dumps(dirty, ensure_ascii=False))
    assert {"phone", "messaging_handle", "platform_url", "email", "forbidden_key"} <= {v.kind for v in t}
    assert all(len(v.sample) <= 80 for v in t)  # samples never reproduce the full value


def test_provenance_anchor_hash_is_allowed_but_other_hashes_are_not():
    ok = {"first_payload_hash": "e" * 64, "checksum": "f" * 64}
    assert check_records(ok) == []
    bad = {"some_id": "e" * 64}
    assert [v.kind for v in check_records(bad)] == ["unexpected_hash"]
