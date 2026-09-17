"""RULE_V1 — tier-1 extraction (001C §5–§7). Pure: text in, Observation out.

    obs = extract_observation(text, record_type="post", author_name=None, fx=None)

`fx` is an optional callable `(currency, date_iso) -> (rate_lak_per_unit, rate_date, source) | None`
so LAK normalisation (001C §6) stays testable without a database. Without it, non-LAK prices
keep their original amount and `normalisation_status = "NO_FX"`.
"""
from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from .classify import classify_asset, classify_signal
from .fields import (
    METHOD, RULES_VERSION, extract_area, extract_contact, extract_location_text, extract_map_and_coords, extract_price,
    extract_title_mention,
)
from .keywords import KEYWORD_GROUPS, KEYWORD_GROUPS_VERSION, exclude_hits, group_hits, norm
from .models import Claim, Observation, PriceObservation, Span
from .numbers import nfc, span16

FxLookup = Callable[[str, str | None], tuple[Decimal, str, str] | None]

_ROLE_TERMS = {
    "OWNER_CLAIMED": KEYWORD_GROUPS["owner"],
    "DEVELOPER": ("ໂຄງການ", "developer", "ຜູ້ພັດທະນາ"),
    "COMPANY_AGENT": ("ບໍລິສັດ", "co.,ltd", "co., ltd", "company", "real estate", "ອະສັງຫາ", "property"),
    "FREELANCE_AGENT": ("ນາຍໜ້າ", "agent", "broker", "ຕົວແທນ", "ຝາກຂາຍ"),
}


def extract_observation(
    text: str | None,
    record_type: str = "post",
    author_name: str | None = None,
    post_date: str | None = None,
    fx: FxLookup | None = None,
    contact_salt: str = "",
) -> Observation:
    t = nfc(text)
    groups = group_hits(t)
    excludes = exclude_hits(t)

    claims: list[Claim] = []
    claims += extract_price(t)
    claims += extract_area(t)
    claims += extract_location_text(t)
    claims += extract_map_and_coords(t)
    claims += extract_contact(t, contact_salt)
    claims += extract_title_mention(t)

    # transaction type + advertiser role are whole-text claims: span = first triggering term
    tx = _transaction_type(t, groups)
    if tx:
        claims.append(tx)
    role = _advertiser_role(t, author_name)
    if role:
        claims.append(role)

    asset_type, asset_conf, asset_signals = classify_asset(t)
    if asset_type != "UNKNOWN":
        claims.append(_term_claim(t, "ASSET_TYPE", _first_term(t, _asset_trigger_terms(asset_type)), asset_conf, asset_type=asset_type))

    prices = _price_observations(claims, tx, fx, post_date)
    signal, sig_conf, sig_signals = classify_signal(groups, excludes, record_type, price_normalised=any(p.amount_lak for p in prices))
    prices = _type_prices(prices, signal)

    obs = Observation(
        signal_class=signal,
        signal_confidence=sig_conf,
        asset_type=asset_type,
        asset_confidence=round(asset_conf, 3),
        extraction_method=METHOD,
        method_version=RULES_VERSION,
        keyword_groups_version=KEYWORD_GROUPS_VERSION,
        claims=claims,
        price_observations=prices,
        signals=sig_signals + asset_signals,
        group_hits=groups,
    )
    return obs


# ---------------------------------------------------------------- whole-text claims

def _first_term(text: str, terms: tuple[str, ...] | list[str]) -> tuple[int, int] | None:
    hay = norm(text)
    best: tuple[int, int] | None = None
    for term in terms:
        i = hay.find(norm(term))
        if i >= 0 and (best is None or i < best[0]):
            best = (i, i + len(norm(term)))
    return best


def _term_claim(text: str, field: str, span: tuple[int, int] | None, conf: float, **cols) -> Claim | None:
    if span is None:
        return None
    s16, e16 = span16(text, *span)
    return Claim(field=field, value_text=text[span[0]:span[1]], evidence_span=Span(s16, e16), extraction_method=METHOD,
                 method_version=RULES_VERSION, confidence=round(conf, 3), normalised=dict(cols))


def _transaction_type(text: str, groups: dict[str, list[str]]) -> Claim | None:
    sale, rent, want = "intent_sale" in groups, "intent_rent" in groups, "intent_want" in groups
    if want and rent and not sale:
        tt, terms, conf = "WANTED_RENT", groups["intent_want"], 0.70
    elif want and not sale:
        tt, terms, conf = "WANTED_BUY", groups["intent_want"], 0.70
    elif sale and not rent:
        tt, terms, conf = "SALE", groups["intent_sale"], 0.85
    elif rent and not sale:
        tt, terms, conf = "RENT", groups["intent_rent"], 0.85
    elif sale and rent:
        tt, terms, conf = "SALE", groups["intent_sale"], 0.55
    else:
        return None
    return _term_claim(text, "TRANSACTION_TYPE", _first_term(text, terms), conf, transaction_type=tt)


def _advertiser_role(text: str, author_name: str | None) -> Claim | None:
    for role, terms in _ROLE_TERMS.items():
        span = _first_term(text, terms)
        if span:
            conf = 0.75 if role == "OWNER_CLAIMED" else 0.65
            return _term_claim(text, "ADVERTISER_ROLE", span, conf, advertiser_role=role, source="TEXT")
    if author_name:
        a = norm(author_name)
        if any(norm(t) in a for t in _ROLE_TERMS["COMPANY_AGENT"]):
            # evidence is the author name, not the text: span points at text start (length 1) and source says so
            return Claim(field="ADVERTISER_ROLE", value_text=author_name, evidence_span=Span(0, 1), extraction_method=METHOD,
                         method_version=RULES_VERSION, confidence=0.45,
                         normalised={"advertiser_role": "COMPANY_AGENT", "source": "AUTHOR_NAME"}) if text else None
    return None


def _asset_trigger_terms(asset_type: str) -> tuple[str, ...]:
    from .classify import ASSET_TERMS
    return ASSET_TERMS.get(asset_type, ())


# ---------------------------------------------------------------- prices

def _price_observations(claims: list[Claim], tx: Claim | None, fx: FxLookup | None, post_date: str | None) -> list[PriceObservation]:
    out: list[PriceObservation] = []
    area = next((Decimal(str(c.normalised["area_sqm"])) for c in claims if c.field == "AREA" and c.confidence >= 0.6), None)
    for i, c in enumerate(claims):
        if c.field != "PRICE" or c.normalised.get("price_withheld"):
            continue
        amount = Decimal(str(c.normalised["amount_original"]))
        cur = c.normalised["currency_original"]
        basis = c.normalised["price_basis"]
        po = PriceObservation(price_type="UNCLASSIFIED_PRICE", amount_original=amount, currency_original=cur, price_basis=basis,
                              confidence=c.confidence, claim_index=i)
        if cur == "LAK":
            po.amount_lak, po.fx_rate, po.fx_rate_date, po.fx_source = amount, Decimal(1), post_date, "IDENTITY"
        elif cur in ("THB", "USD") and fx is not None:
            r = fx(cur, post_date)
            if r:
                rate, rdate, src = r
                po.amount_lak, po.fx_rate, po.fx_rate_date, po.fx_source = (amount * rate).quantize(Decimal("1")), rate, rdate, src
            else:
                c.normalisation_status = "NO_FX"
        elif cur in ("THB", "USD"):
            c.normalisation_status = "NO_FX"
        if po.amount_lak is not None and area and area > 0:
            if basis == "TOTAL":
                po.price_per_sqm_lak = (po.amount_lak / area).quantize(Decimal("1"))
            elif basis == "PER_SQM":
                po.price_per_sqm_lak = po.amount_lak
        out.append(po)
    return out


def _type_prices(prices: list[PriceObservation], signal: str) -> list[PriceObservation]:
    for p in prices:
        if signal == "PROPERTY_SALE":
            p.price_type = "ASKING_SALE_PER_SQM" if p.price_basis == "PER_SQM" else "ASKING_SALE"
        elif signal == "PROPERTY_RENT":
            p.price_type = "ASKING_RENT_YEARLY" if p.price_basis == "PER_YEAR" else "ASKING_RENT_MONTHLY"
        elif signal == "PROPERTY_WANTED":
            p.price_type = "WANTED_BUDGET"
        else:
            p.price_type = "UNCLASSIFIED_PRICE"
    return prices
