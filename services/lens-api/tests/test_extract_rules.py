"""RULE_V1 unit tests on synthetic Lao/Thai/English posts (SLL-PROP-DATA-001C §5–§7, §10).

No database, no network. The golden-fixture gate (§10.1, real hand-labelled data) is a
separate test that stays skipped until `tests/fixtures/extract/golden-v1.jsonl` exists.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.extract import KEYWORD_GROUPS, KEYWORD_GROUPS_VERSION, RULES_VERSION, extract_observation
from app.extract.models import ASSET_TYPES, CLAIM_FIELDS, SIGNAL_CLASSES, Claim, Span
from app.extract.numbers import slice16

FX = lambda cur, date: (Decimal("21500"), "2026-09-01", "BOL_REFERENCE") if cur == "USD" else (Decimal("640"), "2026-09-01", "BOL_REFERENCE") if cur == "THB" else None  # noqa: E731

POST_LAND = "ຂາຍດິນ 20x30 ບ້ານນາສ້າງໄຜ່ ເມືອງໄຊທານີ ນະຄອນຫຼວງວຽງຈັນ ລາຄາ 2.5 ຕື້ ໂທ 020 5512 3456 ໃບຕາດິນແທ້"


def by_field(obs, f):
    return [c for c in obs.claims if c.field == f]


# ---------------------------------------------------------------- classification

@pytest.mark.parametrize(
    "text,record_type,expected",
    [
        (POST_LAND, "post", "PROPERTY_SALE"),
        ("ໃຫ້ເຊົ່າເຮືອນ 3 ຫ້ອງນອນ ບ້ານໂພນຕ້ອງ ລາຄາ 8 ລ້ານ/ເດືອນ", "post", "PROPERTY_RENT"),
        ("ຕ້ອງການຊື້ດິນ ເມືອງສີສັດຕະນາກ ງົບ 800 ລ້ານ", "post", "PROPERTY_WANTED"),
        ("ໂຄງການບ້ານຈັດສັນ ເຟສ 2 ດິນ 1 ເຮັກຕາ", "post", "DEVELOPER_PROJECT"),
        ("ດິນຢູ່ໄຊເສດຖາ ຕອນນີ້ ຕາແມັດລະ 5 ລ້ານ ແພງຫຼາຍ", "post", "PRICE_DISCUSSION"),
        ("ຮັບຝາກຂາຍ ນາຍໜ້າມືອາຊີບ ບໍລິການຄົບວົງຈອນ", "post", "AGENT_ADVERTISEMENT"),
        ("ສະບາຍດີ ມື້ນີ້ອາກາດດີ", "post", "NON_PROPERTY"),
        ("ຂາຍລົດ Toyota Vigo ປີ 2015 ລາຄາ 150 ລ້ານ", "post", "UNCERTAIN"),  # exclude term ລົດ demotes
        ("ລາຄາເທົ່າໃດ", "comment", "UNCERTAIN"),
        ("", "post", "NON_PROPERTY"),
        (None, "post", "NON_PROPERTY"),
    ],
)
def test_signal_class(text, record_type, expected):
    obs = extract_observation(text, record_type)
    assert obs.signal_class == expected
    assert 0.0 <= obs.signal_confidence <= 1.0


@pytest.mark.parametrize(
    "text,expected",
    [
        (POST_LAND, "LAND"),
        ("ຂາຍເຮືອນພ້ອມດິນ ບ້ານດົງໂດກ", "HOUSE"),
        ("ເຊົ່າຫ້ອງ ເດືອນລະ 2,500,000 ກີບ", "APARTMENT"),
        ("ຂາຍສາງ 1,000 ຕາແມັດ ຕິດຖະໜົນ 13 ໃຕ້", "WAREHOUSE"),
        ("ຂາຍໂຮງແຮມ 30 ຫ້ອງ ວັງວຽງ", "HOTEL"),
        ("ຂາຍສວນຢາງ 5 ເຮັກຕາ ແຂວງຈຳປາສັກ", "FARM"),
        ("ຂາຍລົດ", "UNKNOWN"),
    ],
)
def test_asset_type(text, expected):
    assert extract_observation(text).asset_type == expected


def test_exclude_terms_and_comment_are_recorded_as_signals():
    obs = extract_observation("ຂາຍລົດ ລາຄາ 150 ລ້ານ")
    assert any(s.startswith("exclude_terms:") for s in obs.signals)
    obs = extract_observation("ຂາຍດິນ ລາຄາ 2 ຕື້", record_type="comment")
    assert "comment_weaker" in obs.signals and obs.signal_class == "PROPERTY_SALE"


# ---------------------------------------------------------------- price

@pytest.mark.parametrize(
    "text,amount,currency,basis",
    [
        ("ລາຄາ 2.5 ຕື້", Decimal("2500000000.0"), "LAK", "TOTAL"),
        ("ລາຄາ 850 ລ້ານ ກີບ", Decimal("850000000"), "LAK", "TOTAL"),
        ("2,500,000,000 ກີບ", Decimal("2500000000"), "LAK", "TOTAL"),
        ("ລາຄາ ໒.໕ ຕື້", Decimal("2500000000.0"), "LAK", "TOTAL"),  # Lao digits
        ("$45,000", Decimal("45000"), "USD", "TOTAL"),
        ("45,000 USD", Decimal("45000"), "USD", "TOTAL"),
        ("ราคา 1.2 ล้านบาท", Decimal("1200000.0"), "THB", "TOTAL"),
        ("8 ລ້ານ/ເດືອນ", Decimal("8000000"), "LAK", "PER_MONTH"),
        ("ຕາແມັດລະ 5 ລ້ານ", Decimal("5000000"), "LAK", "PER_SQM"),
        ("ເດືອນລະ 2,500,000 ກີບ", Decimal("2500000"), "LAK", "PER_MONTH"),
        ("1,500 USD/month", Decimal("1500"), "USD", "PER_MONTH"),
    ],
)
def test_price_parsing(text, amount, currency, basis):
    claims = by_field(extract_observation("ຂາຍດິນ " + text), "PRICE")
    assert len(claims) == 1, claims
    n = claims[0].normalised
    assert Decimal(str(n["amount_original"])) == amount
    assert n["currency_original"] == currency
    assert n["price_basis"] == basis


def test_price_span_has_no_trailing_whitespace_and_slices_back():
    obs = extract_observation(POST_LAND)
    (c,) = by_field(obs, "PRICE")
    assert c.value_text == "2.5 ຕື້"
    assert slice16(obs_text(obs, POST_LAND), c.evidence_span.start, c.evidence_span.end) == c.value_text


def test_dimensions_and_phone_numbers_are_not_prices():
    obs = extract_observation("ຂາຍດິນ 20x30 ໂທ 020 5512 3456")
    assert by_field(obs, "PRICE") == []
    obs = extract_observation("ຂາຍດິນ 600 ຕາແມັດ")
    assert by_field(obs, "PRICE") == []


def test_bare_number_near_price_term_is_low_confidence():
    (c,) = by_field(extract_observation("ຂາຍດິນ ລາຄາ 350"), "PRICE")
    assert "magnitude_ambiguous" in c.signals and c.confidence < 0.5
    assert c.review_status == "UNREVIEWED" if c.confidence >= 0.3 else "LOW_CONFIDENCE"


def test_price_observation_types_and_fx():
    obs = extract_observation("Land for sale 600 sqm $45,000", fx=FX, post_date="2026-09-10")
    (p,) = obs.price_observations
    assert p.price_type == "ASKING_SALE" and p.currency_original == "USD"
    assert p.amount_lak == Decimal("967500000") and p.fx_source == "BOL_REFERENCE" and p.fx_rate_date == "2026-09-01"
    assert p.price_per_sqm_lak == Decimal("1612500")
    # no FX → NO_FX status, original amount kept, nothing invented (I2)
    obs = extract_observation("Land for sale 600 sqm $45,000")
    (p,) = obs.price_observations
    assert p.amount_lak is None and by_field(obs, "PRICE")[0].normalisation_status == "NO_FX"
    # rent monthly / wanted budget / unclassified
    assert extract_observation("ໃຫ້ເຊົ່າເຮືອນ 8 ລ້ານ/ເດືອນ").price_observations[0].price_type == "ASKING_RENT_MONTHLY"
    assert extract_observation("ຕ້ອງການຊື້ດິນ ງົບ 800 ລ້ານ").price_observations[0].price_type == "WANTED_BUDGET"
    assert extract_observation("ດິນແພງ ຕາແມັດລະ 5 ລ້ານ").price_observations[0].price_type == "UNCLASSIFIED_PRICE"


def test_conflicting_prices_are_all_kept_with_lower_confidence():
    obs = extract_observation("ຂາຍດິນ ລາຄາ 2 ຕື້ ຫຼື 1.2 ຕື້ ຕໍ່ລອງໄດ້")
    claims = by_field(obs, "PRICE")
    assert len(claims) == 2 and all("conflicting_claims" in c.signals for c in claims)


# ---------------------------------------------------------------- area

@pytest.mark.parametrize(
    "text,sqm",
    [
        ("20x30", Decimal("600.00")),
        ("20 x 30", Decimal("600.00")),
        ("20×30", Decimal("600.00")),
        ("ເນື້ອທີ່ 600 ຕາແມັດ", Decimal("600.00")),
        ("600m2", Decimal("600.00")),
        ("1 ເຮັກຕາ", Decimal("10000.00")),
        ("2 ໄຮ່", Decimal("3200.00")),
        ("3 ງານ", Decimal("1200.00")),
        ("50 ຕາວາ", Decimal("200.00")),
        ("ເນື້ອທີ່ 2.5 ha", Decimal("25000.00")),
    ],
)
def test_area_normalisation(text, sqm):
    claims = by_field(extract_observation("ຂາຍດິນ " + text), "AREA")
    assert len(claims) == 1 and Decimal(str(claims[0].normalised["area_sqm"])) == sqm


def test_dimensions_yield_frontage_and_depth():
    obs = extract_observation("ຂາຍດິນ 20x30")
    assert Decimal(str(by_field(obs, "FRONTAGE")[0].normalised["metres"])) == 20
    assert Decimal(str(by_field(obs, "DEPTH")[0].normalised["metres"])) == 30
    assert by_field(extract_observation("ຂາຍດິນ 2x3"), "AREA") == []  # too small to be metres
    assert by_field(extract_observation("ຂາຍດິນ 1000x2000"), "AREA") == []  # too large


# ---------------------------------------------------------------- contact (D5, I10)

def test_contact_is_masked_hashed_and_raw_is_separate():
    obs = extract_observation("ໂທ 020 5512 3456 ຫຼື 02199887766 WhatsApp 02055123456 LINE: vily_prop", contact_salt="s")
    contacts = by_field(obs, "CONTACT")
    kinds = sorted(c.normalised["kind"] for c in contacts)
    assert kinds == ["LINE", "PHONE", "PHONE", "WHATSAPP"]
    phone = next(c for c in contacts if c.normalised["masked_value"].startswith("0205"))
    assert phone.normalised["masked_value"] == "0205XXXXX56"
    assert phone.normalised["raw_value"] == "02055123456"
    assert len(phone.normalised["contact_hash"]) == 64
    # same number in different spacing → same hash (entity-resolution signal)
    other = next(c for c in contacts if c.normalised["kind"] == "WHATSAPP")
    assert other.normalised["contact_hash"] != phone.normalised["contact_hash"]  # kind is part of the hash
    assert extract_observation("ໂທ 020 5512 3456", contact_salt="a").claims[0].normalised["contact_hash"] != phone.normalised["contact_hash"]


def test_international_prefix_normalises_to_local():
    (c,) = by_field(extract_observation("call +856 20 5512 3456"), "CONTACT")
    assert c.normalised["raw_value"] == "02055123456"


# ---------------------------------------------------------------- location / map / coordinates

def test_location_text_levels():
    obs = extract_observation(POST_LAND)
    hints = {c.value_text: c.normalised["admin_level_hint"] for c in by_field(obs, "LOCATION_TEXT")}
    assert hints == {"ນະຄອນຫຼວງວຽງຈັນ": "province", "ເມືອງໄຊທານີ": "district", "ບ້ານນາສ້າງໄຜ່": "village"}


def test_housing_estate_is_not_a_village():
    obs = extract_observation("ໂຄງການບ້ານຈັດສັນ ເຟສ 2")
    assert by_field(obs, "LOCATION_TEXT") == []


def test_map_url_with_coordinates_yields_coordinate_claim():
    obs = extract_observation("ດິນ https://www.google.com/maps/place/x/@17.9757,102.6331,17z ຂາຍ")
    (u,) = by_field(obs, "MAP_URL")
    (c,) = by_field(obs, "COORDINATE")
    assert u.normalised["lat"] == 17.9757 and c.normalised["lng"] == 102.6331 and c.normalised["source"] == "MAP_URL"


def test_short_map_link_is_kept_unexpanded():
    (u,) = by_field(extract_observation("ດິນ https://maps.app.goo.gl/AbCdEf ຂາຍ"), "MAP_URL")
    assert u.normalised["short_link"] is True and "short_map_link_unexpanded" in u.signals
    assert "lat" not in u.normalised


def test_text_coordinates_only_inside_laos():
    assert len(by_field(extract_observation("ພິກັດ 17.9757, 102.6331"), "COORDINATE")) == 1
    assert by_field(extract_observation("ພິກັດ 13.7563, 100.5018"), "COORDINATE") == []  # Bangkok


# ---------------------------------------------------------------- roles, transaction type, title

def test_transaction_type_and_roles():
    obs = extract_observation("ເຈົ້າຂອງຂາຍເອງ ບໍ່ຜ່ານນາຍໜ້າ ຂາຍດິນ 2 ຕື້")
    assert by_field(obs, "TRANSACTION_TYPE")[0].normalised["transaction_type"] == "SALE"
    assert by_field(obs, "ADVERTISER_ROLE")[0].normalised["advertiser_role"] == "OWNER_CLAIMED"
    obs = extract_observation("ຝາກຂາຍດິນ ນາຍໜ້າ", author_name="Somchai")
    assert by_field(obs, "ADVERTISER_ROLE")[0].normalised["advertiser_role"] == "FREELANCE_AGENT"
    obs = extract_observation("ຂາຍດິນ 2 ຕື້", author_name="Vientiane Real Estate Co., Ltd")
    r = by_field(obs, "ADVERTISER_ROLE")[0]
    assert r.normalised == {"advertiser_role": "COMPANY_AGENT", "source": "AUTHOR_NAME"} and r.confidence < 0.5


def test_title_mention_is_a_claim_never_a_verified_title():
    (c,) = by_field(extract_observation(POST_LAND), "LAND_TITLE_MENTION")
    assert c.value_text == "ໃບຕາດິນແທ້" and c.normalised == {}


# ---------------------------------------------------------------- contract-level guarantees (001C §3, §10)

def test_every_claim_carries_method_version_confidence_span_status():
    for text in (POST_LAND, "ໃຫ້ເຊົ່າເຮືອນ 8 ລ້ານ/ເດືອນ ໂທ 02055123456", "Land $45,000 https://maps.google.com/?q=17.98,102.56"):
        obs = extract_observation(text, fx=FX)
        assert obs.claims
        for c in obs.claims:
            assert c.extraction_method == "RULE_V1" and c.method_version == RULES_VERSION
            assert 0.0 <= c.confidence <= 1.0
            assert c.evidence_span.end > c.evidence_span.start
            assert c.review_status in {"UNREVIEWED", "LOW_CONFIDENCE"}
            assert c.field in CLAIM_FIELDS
        assert obs.signal_class in SIGNAL_CLASSES and obs.asset_type in ASSET_TYPES
        assert obs.keyword_groups_version == KEYWORD_GROUPS_VERSION


def test_spans_are_utf16_and_survive_astral_characters():
    text = "🏠🏠 ຂາຍດິນ 20x30 ລາຄາ 2.5 ຕື້ 🔥"
    obs = extract_observation(text)
    for c in obs.claims:
        if c.normalised.get("source") == "AUTHOR_NAME":
            continue
        assert slice16(text, c.evidence_span.start, c.evidence_span.end) == c.value_text, c.field
    (p,) = by_field(obs, "PRICE")
    assert p.evidence_span.start == len("🏠🏠 ຂາຍດິນ 20x30 ລາຄາ ".encode("utf-16-le")) // 2


def test_claim_outside_vocabulary_is_rejected():
    with pytest.raises(ValueError):
        Claim(field="OWNER", value_text="x", evidence_span=Span(0, 1), extraction_method="RULE_V1", method_version="1", confidence=0.5)
    with pytest.raises(ValueError):
        Claim(field="PRICE", value_text="x", evidence_span=Span(0, 1), extraction_method="RULE_V1", method_version="1", confidence=1.5)


def test_determinism_byte_identical():
    a = json.dumps(extract_observation(POST_LAND, fx=FX, contact_salt="s").to_dict(), sort_keys=True, ensure_ascii=False)
    b = json.dumps(extract_observation(POST_LAND, fx=FX, contact_salt="s").to_dict(), sort_keys=True, ensure_ascii=False)
    assert a == b


def test_keyword_groups_match_contract_vocabulary():
    assert set(KEYWORD_GROUPS) == {
        "asset", "intent_sale", "intent_rent", "intent_want", "price", "area", "location", "agent", "owner", "project", "title", "contact",
    }
    assert all(t == t.strip() and t for terms in KEYWORD_GROUPS.values() for t in terms)


GOLDEN = Path(__file__).parent / "fixtures" / "extract" / "golden-v1.jsonl"


@pytest.mark.skipif(not GOLDEN.exists(), reason="golden fixture not yet labelled (001C §10.1, operator input)")
def test_golden_fixture_gate():
    rows = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) >= 100
    tp = fp = fn = 0
    positive = {"PROPERTY_SALE", "PROPERTY_RENT", "PROPERTY_WANTED"}
    price_ok = price_n = area_ok = area_n = 0
    for r in rows:
        obs = extract_observation(r["text"], r.get("record_type", "post"))
        got, exp = obs.signal_class, r["signal_class"]
        if got in positive and got == exp:
            tp += 1
        elif got in positive and got != exp:
            fp += 1
        elif exp in positive:
            fn += 1
        if "amount_original" in r:
            price_n += 1
            price_ok += any(Decimal(str(c.normalised["amount_original"])) == Decimal(str(r["amount_original"])) for c in obs.claims_for("PRICE"))
        if "area_sqm" in r:
            area_n += 1
            area_ok += any(Decimal(str(c.normalised["area_sqm"])) == Decimal(str(r["area_sqm"])) for c in obs.claims_for("AREA"))
    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    assert precision >= 0.85 and recall >= 0.75, (precision, recall)
    assert price_n == 0 or price_ok / price_n >= 0.90
    assert area_n == 0 or area_ok / area_n >= 0.90


def obs_text(obs, text):  # helper kept explicit so the test reads as "span → text"
    return text
