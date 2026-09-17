"""Logical L2 records for extraction output (001C §3). Plain dataclasses, JSON-friendly.

Physical tables come in 001F; these shapes are the contract the rules module emits and the
tests assert on. Every Claim carries method, method_version, confidence, evidence span and
review status — a claim without them cannot be constructed (I2).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Literal

SignalClass = Literal[
    "PROPERTY_SALE", "PROPERTY_RENT", "PROPERTY_WANTED", "AGENT_ADVERTISEMENT", "DEVELOPER_PROJECT",
    "PRICE_DISCUSSION", "MARKET_INFORMATION", "NON_PROPERTY", "UNCERTAIN",
]
AssetType = Literal[
    "LAND", "HOUSE", "APARTMENT", "COMMERCIAL", "WAREHOUSE", "HOTEL", "FARM", "DEVELOPMENT_LAND",
    "BUILDING", "OTHER", "UNKNOWN",
]
ClaimField = Literal[
    "PRICE", "AREA", "FRONTAGE", "DEPTH", "TRANSACTION_TYPE", "ASSET_TYPE", "ADVERTISER_ROLE",
    "LOCATION_TEXT", "MAP_URL", "COORDINATE", "CONTACT", "LAND_TITLE_MENTION", "ROAD_ACCESS", "PROJECT_NAME",
]
ReviewStatus = Literal["UNREVIEWED", "LOW_CONFIDENCE", "CONFIRMED", "CORRECTED", "REJECTED"]

CLAIM_FIELDS: frozenset[str] = frozenset(ClaimField.__args__)  # type: ignore[attr-defined]
SIGNAL_CLASSES: frozenset[str] = frozenset(SignalClass.__args__)  # type: ignore[attr-defined]
ASSET_TYPES: frozenset[str] = frozenset(AssetType.__args__)  # type: ignore[attr-defined]

LOW_CONFIDENCE = 0.3


def _dec(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v.normalize()) if v == v.to_integral() else str(v)
    return v


@dataclass(frozen=True)
class Span:
    start: int  # UTF-16 code units, inclusive
    end: int  # exclusive


@dataclass
class Claim:
    field: str
    value_text: str
    evidence_span: Span
    extraction_method: str
    method_version: str
    confidence: float
    review_status: str = "UNREVIEWED"
    # Normalised columns (subset used depends on field) — 001C §6
    normalised: dict[str, Any] = field(default_factory=dict)
    normalisation_status: str = "OK"  # OK | NO_FX | UNPARSED | NOT_APPLICABLE
    signals: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.field not in CLAIM_FIELDS:
            raise ValueError(f"claim field outside 001C vocabulary: {self.field}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be within [0,1]")
        if self.evidence_span.end <= self.evidence_span.start:
            raise ValueError("evidence span must be non-empty")
        if self.confidence < LOW_CONFIDENCE and self.review_status == "UNREVIEWED":
            self.review_status = "LOW_CONFIDENCE"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["normalised"] = {k: _dec(v) for k, v in self.normalised.items()}
        return d


@dataclass
class PriceObservation:
    price_type: str  # ASKING_SALE | ASKING_RENT_MONTHLY | ASKING_RENT_YEARLY | ASKING_SALE_PER_SQM | WANTED_BUDGET | UNCLASSIFIED_PRICE
    amount_original: Decimal
    currency_original: str  # LAK | THB | USD | UNKNOWN
    price_basis: str  # TOTAL | PER_SQM | PER_MONTH | PER_YEAR | UNKNOWN
    confidence: float
    claim_index: int  # index into Observation.claims
    amount_lak: Decimal | None = None
    fx_rate: Decimal | None = None
    fx_rate_date: str | None = None
    fx_source: str | None = None
    price_per_sqm_lak: Decimal | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: _dec(v) for k, v in asdict(self).items()}


@dataclass
class Observation:
    signal_class: str
    signal_confidence: float
    asset_type: str
    asset_confidence: float
    extraction_method: str
    method_version: str
    keyword_groups_version: str
    claims: list[Claim] = field(default_factory=list)
    price_observations: list[PriceObservation] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    group_hits: dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.signal_class not in SIGNAL_CLASSES:
            raise ValueError(f"signal class outside vocabulary: {self.signal_class}")
        if self.asset_type not in ASSET_TYPES:
            raise ValueError(f"asset type outside vocabulary: {self.asset_type}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_class": self.signal_class,
            "signal_confidence": round(self.signal_confidence, 3),
            "asset_type": self.asset_type,
            "asset_confidence": round(self.asset_confidence, 3),
            "extraction_method": self.extraction_method,
            "method_version": self.method_version,
            "keyword_groups_version": self.keyword_groups_version,
            "claims": [c.to_dict() for c in self.claims],
            "price_observations": [p.to_dict() for p in self.price_observations],
            "signals": list(self.signals),
            "group_hits": self.group_hits,
        }

    def claims_for(self, field_name: str) -> list[Claim]:
        return [c for c in self.claims if c.field == field_name]
