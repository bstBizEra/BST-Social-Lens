"""Signal-class and asset-type classification (001C §4, §5.1). Deterministic decision table."""
from __future__ import annotations

from .keywords import norm

# asset-type groups (001C §4.2); order = reporting order, score = number of distinct terms hit
ASSET_TERMS: dict[str, tuple[str, ...]] = {
    "APARTMENT": ("ອາພາດເມັນ", "ຄອນໂດ", "ຫ້ອງເຊົ່າ", "ເຊົ່າຫ້ອງ", "ຫ້ອງພັກ", "ຫ້ອງແຖວ", "condo", "apartment", "คอนโด"),
    "COMMERCIAL": ("ຮ້ານ", "ຕຶກແຖວ", "ອາຄານພານິດ", "shophouse", "ຫ້ອງການ", "office", "ຕະຫຼາດ"),
    "WAREHOUSE": ("ສາງ", "warehouse", "โกดัง"),
    "HOTEL": ("ໂຮງແຮມ", "ເກດເຮົ້າ", "ຣີສອດ", "hotel", "guesthouse", "resort"),
    "FARM": ("ສວນ", "ໄຮ່", "ນາ ", "ຟາມ", "farm", "ສວນຢາງ", "ສວນກາເຟ"),
    "DEVELOPMENT_LAND": ("ໂຄງການດິນ", "ດິນໂຄງການ", "ດິນຈັດສັນ", "ບ້ານຈັດສັນ", "ດິນແບ່ງຂາຍ", "development land", "housing project"),
    "BUILDING": ("ຕຶກ", "ອາຄານ", "building"),
    "HOUSE": ("ເຮືອນ", "ບ້ານພ້ອມດິນ", "ບ້ານດ່ຽວ", "ວິນລາ", "villa", "house", "บ้าน", "townhouse", "ທາວເຮົ້າ"),
    "LAND": ("ດິນ", "ທີ່ດິນ", "ດິນເປົ່າ", "land", "ที่ดิน", "ไร่"),
}
_ASSET_BASE = {"LAND": 0.80, "HOUSE": 0.80, "APARTMENT": 0.85, "COMMERCIAL": 0.75, "WAREHOUSE": 0.85, "HOTEL": 0.85,
               "FARM": 0.70, "DEVELOPMENT_LAND": 0.80, "BUILDING": 0.65}


def classify_asset(text: str) -> tuple[str, float, list[str]]:
    hay = norm(text)
    if not hay:
        return "UNKNOWN", 0.0, []
    scores: dict[str, int] = {}
    for atype, terms in ASSET_TERMS.items():
        n = sum(1 for t in terms if norm(t) in hay)
        if n:
            scores[atype] = n
    if not scores:
        return "UNKNOWN", 0.0, []
    # Specific types beat the generic ones they contain (ບ້ານ is also a village prefix; ດິນ is everywhere).
    for generic, specifics in (("LAND", ("DEVELOPMENT_LAND", "FARM", "HOUSE", "APARTMENT", "COMMERCIAL", "WAREHOUSE", "HOTEL")),
                               ("HOUSE", ("DEVELOPMENT_LAND",)),
                               ("BUILDING", ("COMMERCIAL", "HOTEL", "APARTMENT", "WAREHOUSE"))):
        if generic in scores and any(s in scores for s in specifics):
            scores[generic] -= 1
            if scores[generic] <= 0:
                del scores[generic]
    if not scores:
        return "UNKNOWN", 0.0, []
    best = max(scores.values())
    winners = [a for a, s in scores.items() if s == best]
    signals = [f"asset_candidate:{a}" for a in scores]
    if len(winners) > 1:
        # HOUSE + LAND together is the common "house with land" listing → HOUSE
        if set(winners) == {"HOUSE", "LAND"}:
            return "HOUSE", 0.70, signals + ["house_with_land"]
        return "UNKNOWN", 0.40, signals + ["asset_tie"]
    w = winners[0]
    return w, min(1.0, _ASSET_BASE[w] + 0.05 * (best - 1)), signals


def classify_signal(groups: dict[str, list[str]], excludes: list[str], record_type: str, price_normalised: bool) -> tuple[str, float, list[str]]:
    """001C §5.1 decision table + adjustments. Returns (class, confidence, signals)."""
    has = lambda g: g in groups  # noqa: E731
    asset, sale, rent, want = has("asset"), has("intent_sale"), has("intent_rent"), has("intent_want")
    lead = has("price") or has("area") or has("location")
    agent, project = has("agent"), has("project")
    signals: list[str] = []

    if asset and sale and not rent and not want:
        cls, conf = "PROPERTY_SALE", 0.80
    elif asset and rent and not sale and not want:
        cls, conf = "PROPERTY_RENT", 0.80
    elif asset and want and not sale and not rent:
        cls, conf = "PROPERTY_WANTED", 0.75
    elif asset and sale and rent and not want:
        cls, conf = "PROPERTY_SALE", 0.60
        signals.append("both_intents")
    elif asset and want and (sale or rent):
        # "ຮັບຊື້/ຕ້ອງການ" alongside sale words is usually an agent sourcing listings
        cls, conf = "PROPERTY_WANTED", 0.55
        signals.append("mixed_want_and_offer")
    elif asset and not (sale or rent or want) and lead and project:
        cls, conf = "DEVELOPER_PROJECT", 0.65
    elif asset and not (sale or rent or want) and lead and not agent and not project:
        cls, conf = "PRICE_DISCUSSION", 0.55
    elif not asset and not lead and agent:
        cls, conf = "AGENT_ADVERTISEMENT", 0.60
    elif not asset and lead:
        cls, conf = "UNCERTAIN", 0.40
    elif not asset and not lead and not agent and not project and not (sale or rent or want):
        cls, conf = "NON_PROPERTY", 0.90
    else:
        cls, conf = "UNCERTAIN", 0.40

    if price_normalised and cls not in ("NON_PROPERTY",):
        conf += 0.10
        signals.append("price_normalised")
    extra_leads = sum(1 for g in ("price", "area", "location") if has(g)) - 1
    if extra_leads > 0 and cls not in ("NON_PROPERTY",):
        conf += min(0.15, 0.05 * extra_leads)
        signals.append(f"extra_leads:{extra_leads}")
    if excludes:
        conf -= 0.15
        signals.append("exclude_terms:" + ",".join(excludes))
    if record_type == "comment" and cls not in ("NON_PROPERTY",):
        conf -= 0.10
        signals.append("comment_weaker")
    conf = max(0.0, min(1.0, conf))
    if conf < 0.5 and cls not in ("NON_PROPERTY", "UNCERTAIN"):
        signals.append(f"demoted_from:{cls}")
        cls = "UNCERTAIN"
    return cls, round(conf, 3), signals
