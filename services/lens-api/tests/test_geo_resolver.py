"""GEO_RULE_V1 text path + reconcile on the sample gazetteer (001D §4–§6, §9). No DB."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.extract import extract_observation
from app.geo.gazetteer import Gazetteer, normalise_name, similarity
from app.geo.resolver import RESOLVER_VERSION, resolve

FIX = Path(__file__).parent / "fixtures" / "geo" / "admin-sample.json"


@pytest.fixture(scope="module")
def gaz() -> Gazetteer:
    return Gazetteer.from_fixture(FIX)


def run(text: str, gaz: Gazetteer | None):
    return resolve(extract_observation(text).claims, gaz)


def primary(rs):
    (p,) = [r for r in rs if r.is_primary]
    return p


# ---------------------------------------------------------------- normalisation

def test_normalise_strips_prefix_and_tone_marks():
    assert normalise_name("ບ້ານນາສ້າງໄຜ່", "village") == normalise_name("ນາສາງໄຜ", "village")
    assert normalise_name("ເມືອງ ໄຊທານີ", "district") == normalise_name("ໄຊທານີ", "district")
    assert normalise_name("Ban Dongdok", "village") == "dongdok"
    assert similarity("ໄຊທານີ", "ໄຊທານີ") == 1.0 and 0.5 < similarity("ນາສາງໄຜ", "ນາສາງໄຜ່ໃຫມ") < 1.0


# ---------------------------------------------------------------- text path

def test_full_hierarchy_resolves_to_village(gaz):
    p = primary(run("ຂາຍດິນ 20x30 ບ້ານນາສ້າງໄຜ່ ເມືອງໄຊທານີ ນະຄອນຫຼວງວຽງຈັນ ລາຄາ 2.5 ຕື້", gaz))
    assert (p.precision, p.province_code, p.district_code, p.village_code) == ("VILLAGE", "P-VTE", "D-VTE-XTN", "V-XTN-NSP")
    assert p.point_source == "VILLAGE_CENTROID" and p.lat and p.lng
    assert p.confidence == 0.85 and "village_exact" in p.signals and p.review_status == "UNREVIEWED"
    assert p.resolver_version == RESOLVER_VERSION


def test_village_alone_fills_parents_from_hierarchy(gaz):
    p = primary(run("ຂາຍດິນ ບ້ານດົງໂດກ", gaz))
    assert (p.district_code, p.province_code) == ("D-VTE-XTN", "P-VTE") and "district_from_village" in p.signals


def test_ambiguous_village_without_district_stays_text_only(gaz):
    p = primary(run("ໃຫ້ເຊົ່າເຮືອນ ບ້ານໂພນຕ້ອງ", gaz))
    assert p.precision == "TEXT_ONLY" and p.village_code is None
    assert any(s.startswith("village_ambiguous:") for s in p.signals) and p.review_status == "LOW_CONFIDENCE"


def test_ambiguous_village_disambiguated_by_district(gaz):
    p = primary(run("ໃຫ້ເຊົ່າເຮືອນ ບ້ານໂພນຕ້ອງ ເມືອງໄຊເສດຖາ", gaz))
    assert p.village_code == "V-XST-PTG" and "village_disambiguated_by_district" in p.signals


def test_full_province_name_beats_bare_variant(gaz):
    assert primary(run("ຂາຍດິນ ນະຄອນຫຼວງວຽງຈັນ", gaz)).province_code == "P-VTE"
    assert primary(run("ຂາຍດິນ ແຂວງວຽງຈັນ ເມືອງວັງວຽງ", gaz)).province_code == "P-VTP"


def test_unknown_village_falls_back_to_district(gaz):
    p = primary(run("ຂາຍດິນ ບ້ານບໍ່ມີໃນລະບົບ ເມືອງໄຊທານີ", gaz))
    assert p.precision == "DISTRICT" and p.district_code == "D-VTE-XTN" and "village_unmatched" in p.signals


def test_hierarchy_conflict_lowers_confidence(gaz):
    p = primary(run("ຂາຍດິນ ບ້ານດົງໂດກ ເມືອງປາກເຊ", gaz))  # Dongdok is in Xaythany, not Pakse
    assert "hierarchy_conflict:village_district" in p.signals and p.confidence < 0.85


def test_fuzzy_match_is_flagged(gaz):
    p = primary(run("ຂາຍດິນ ເມືອງໄຊທານີີ", gaz))  # extra vowel: exact fails, bigram similarity passes
    assert p.district_code == "D-VTE-XTN" and any(s.startswith("district_fuzzy:") for s in p.signals) and p.confidence == 0.55


def test_no_gazetteer_yields_text_only(gaz):
    p = primary(run("ຂາຍດິນ ບ້ານດົງໂດກ", None))
    assert p.precision == "TEXT_ONLY" and "no_gazetteer" in p.signals
    p = primary(run("ຂາຍດິນ ບ້ານດົງໂດກ", Gazetteer(None)))
    assert p.precision == "TEXT_ONLY"


def test_no_location_claims_no_rows(gaz):
    assert run("ຂາຍດິນ ລາຄາ 2 ຕື້", gaz) == []


# ---------------------------------------------------------------- point path + reconcile

def test_point_and_text_agree(gaz):
    rs = run("ຂາຍດິນ ບ້ານດົງໂດກ https://maps.google.com/?q=18.052,102.661", gaz)
    p = primary(rs)
    assert p.precision == "EXACT_COORDINATE" and p.point_source == "MAP_URL" and p.village_code == "V-XTN-DDK"
    assert any(s.startswith("point_near_text_centroid") for s in p.signals) and "no_polygons" in p.signals
    t = next(r for r in rs if not r.is_primary)
    assert t.precision == "VILLAGE" and "point_text_agree" in t.signals and t.confidence == 0.95


def test_point_and_text_conflict_keeps_both_low_confidence(gaz):
    rs = run("ຂາຍດິນ ບ້ານດົງໂດກ https://maps.google.com/?q=15.12,105.78", gaz)
    assert len(rs) == 2 and all(r.review_status == "LOW_CONFIDENCE" for r in rs)
    p = primary(rs)
    assert p.precision == "EXACT_COORDINATE" and p.village_code is None and any(s.startswith("point_far_from_text_centroid") for s in p.signals)
    assert "conflict_text_vs_point" in next(r for r in rs if not r.is_primary).signals


def test_viewport_only_url_is_parcel_approximate(gaz):
    p = primary(run("ດິນ https://www.google.com/maps/@17.9757,102.6331,13z ຂາຍ", gaz))
    assert p.precision == "PARCEL_APPROXIMATE" and "viewport_only_zoom:13" in p.signals and p.confidence == 0.8


def test_short_link_only_is_text_only(gaz):
    p = primary(run("ດິນ https://maps.app.goo.gl/AbCdEf ຂາຍ", gaz))
    assert p.precision == "TEXT_ONLY" and "short_map_link_unexpanded" in p.signals


# ---------------------------------------------------------------- contract guarantees (001D §9)

def test_every_resolution_has_precision_confidence_and_inputs(gaz):
    for text in ("ຂາຍດິນ ບ້ານດົງໂດກ", "ໃຫ້ເຊົ່າເຮືອນ ບ້ານໂພນຕ້ອງ", "ດິນ https://maps.google.com/?q=18.05,102.66 ຂາຍ", "ຂາຍດິນ ແຂວງຈໍາປາສັກ"):
        for r in run(text, gaz):
            assert r.precision in {"EXACT_COORDINATE", "PARCEL_APPROXIMATE", "VILLAGE", "DISTRICT", "PROVINCE", "TEXT_ONLY", "UNKNOWN"}
            assert 0.0 <= r.confidence <= 1.0 and r.input_claim_indexes
            if r.precision in ("EXACT_COORDINATE", "PARCEL_APPROXIMATE"):
                assert r.lat is not None and r.lng is not None


def test_determinism(gaz):
    a = json.dumps([r.__dict__ for r in run("ຂາຍດິນ ບ້ານດົງໂດກ https://maps.google.com/?q=18.052,102.661", gaz)], sort_keys=True)
    b = json.dumps([r.__dict__ for r in run("ຂາຍດິນ ບ້ານດົງໂດກ https://maps.google.com/?q=18.052,102.661", gaz)], sort_keys=True)
    assert a == b


GOLDEN = Path(__file__).parent / "fixtures" / "geo" / "golden-v1.jsonl"


@pytest.mark.skipif(not GOLDEN.exists(), reason="golden locations not yet labelled (001D §9.2, operator input)")
def test_golden_locations_gate(gaz):
    rows = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) >= 100
    assigned = district_ok = district_n = village_ok = village_n = 0
    for r in rows:
        rs = run(r["text"], gaz)
        p = primary(rs) if rs else None
        assigned += bool(p and p.precision)
        if "district_code" in r:
            district_n += 1
            district_ok += bool(p and p.district_code == r["district_code"])
        if "village_code" in r:
            village_n += 1
            village_ok += bool(p and p.village_code == r["village_code"])
    assert assigned == len(rows)
    assert district_n == 0 or district_ok / district_n >= 0.85
    assert village_n == 0 or village_ok / village_n >= 0.70


# ---------------------------------------------------------------- PostGIS point path (pure part: applying a point_in_admin row)

def test_apply_polygon_lookup_fills_codes_and_reconciles(gaz):
    from app.geo.resolver import apply_polygon_lookup

    rs = run("ຂາຍດິນ ບ້ານດົງໂດກ https://maps.google.com/?q=18.052,102.661", gaz)
    i = next(k for k, r in enumerate(rs) if r.point_source == "MAP_URL")
    apply_polygon_lookup(rs, {i: {"admin_version": "x", "province_code": "P-VTE", "district_code": "D-VTE-XTN", "village_code": "V-XTN-DDK"}})
    assert rs[i].village_code == "V-XTN-DDK" and "st_within:V-XTN-DDK" in rs[i].signals and "no_polygons" not in rs[i].signals
    t = next(r for r in rs if r.point_source != "MAP_URL")
    assert "point_text_agree" in t.signals
    # polygon says a different district than the text → conflict, text confidence lowered
    rs2 = run("ຂາຍດິນ ບ້ານດົງໂດກ https://maps.google.com/?q=18.052,102.661", gaz)
    j = next(k for k, r in enumerate(rs2) if r.point_source == "MAP_URL")
    apply_polygon_lookup(rs2, {j: {"admin_version": "x", "province_code": "P-CPS", "district_code": "D-CPS-PKS", "village_code": None}})
    t2 = next(r for r in rs2 if r.point_source != "MAP_URL")
    assert "conflict_text_vs_point" in t2.signals and rs2[j].district_code == "D-CPS-PKS"
    # point outside every polygon: marker only, no codes
    rs3 = run("ດິນ https://maps.google.com/?q=18.05,102.66 ຂາຍ", gaz)
    apply_polygon_lookup(rs3, {0: {"admin_version": "x", "province_code": None, "district_code": None, "village_code": None}})
    assert "point_outside_admin_polygons" in rs3[0].signals and rs3[0].village_code is None
    # None lookup (PostGIS absent) leaves the row untouched
    rs4 = run("ດິນ https://maps.google.com/?q=18.05,102.66 ຂາຍ", gaz)
    apply_polygon_lookup(rs4, {0: None})
    assert "no_polygons" in rs4[0].signals
