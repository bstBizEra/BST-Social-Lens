"""Evidence gate + block-rate report (ADR-0004 rule 5) — pure.

GO requires ALL of:
  1. sample     — at least SAMPLE_SIZE targets attempted, sourced from the seen frontier
                  (an experiment file never yields GO; it yields INCONCLUSIVE at best);
  2. block rate — (blocked + error) / eligible ≤ MAX_BLOCK_RATE, where eligible excludes
                  login-wall and not-found (a private group is out of scope by design);
  3. usable     — ok / eligible ≥ MIN_USABLE_RATE — so "1 OK + 49 empty" cannot pass;
  4. value      — against a baseline of what Layers A/B already hold, the OK records
                  add or refresh on average ≥ MIN_INCREMENTAL_FIELDS fields each, with the
                  baseline covering ≥ MIN_BASELINE_COVERAGE of OK records. No baseline →
                  INCONCLUSIVE (never GO).
Any single failed criterion → NO-GO; missing evidence → INCONCLUSIVE.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SAMPLE_SIZE = 50
MAX_BLOCK_RATE = 0.20
MIN_USABLE_RATE = 0.60
MIN_INCREMENTAL_FIELDS = 1.0
MIN_BASELINE_COVERAGE = 0.50

# Fields that count as "value": content + engagement the extension may lack or hold stale.
VALUE_FIELDS = ("text", "author_name", "created_at", "reactions_total", "comments_count", "shares_count", "views_count", "media", "hashtags")
# Engagement fields count as refreshed when the value differs from baseline.
REFRESH_FIELDS = ("reactions_total", "comments_count", "shares_count", "views_count")

Decision = Literal["GO", "NO-GO", "INCONCLUSIVE"]
SampleSource = Literal["frontier", "experiment-file"]


@dataclass
class Attempt:
    url: str
    platform: str
    eligibility: str
    fetcher: str
    status: int
    outcome: str  # ok | login-wall | blocked | not-found | empty | error
    seen_status: str  # fetched | failed | skipped
    fields_filled: list[str] = field(default_factory=list)
    error: str | None = None
    signals: list[str] = field(default_factory=list)
    record_key: str | None = None


def seen_status_for(outcome: str) -> str:
    if outcome == "ok":
        return "fetched"
    if outcome in ("login-wall", "not-found", "empty"):
        return "skipped"  # final: do not retry with a session (rule 3)
    return "failed"  # blocked / error → may retry later


def _present(v: Any) -> bool:
    return v not in (None, "", [], {})


def incremental_value(records: list[dict[str, Any]], baseline: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    """Compare worker records with a baseline keyed by `platform:post_id`.
    Returns coverage (share of OK records that have a baseline row) and the mean number
    of fields added (baseline empty, worker present) or refreshed (engagement differs)."""
    if baseline is None:
        return {"available": False, "coverage": 0.0, "mean_fields_added_or_refreshed": 0.0, "compared": 0}
    compared, total_delta = 0, 0
    per_field: Counter = Counter()
    for r in records:
        b = baseline.get(r.get("key", ""))
        if b is None:
            continue
        compared += 1
        for f in VALUE_FIELDS:
            wv, bv = r.get(f), b.get(f)
            if _present(wv) and not _present(bv):
                total_delta += 1
                per_field[f"added:{f}"] += 1
            elif f in REFRESH_FIELDS and _present(wv) and _present(bv) and wv != bv:
                total_delta += 1
                per_field[f"refreshed:{f}"] += 1
    n = len(records)
    return {
        "available": True,
        "coverage": round(compared / n, 3) if n else 0.0,
        "compared": compared,
        "mean_fields_added_or_refreshed": round(total_delta / compared, 3) if compared else 0.0,
        "per_field": dict(per_field),
    }


def summarize(
    attempts: list[Attempt],
    *,
    source: SampleSource,
    records: list[dict[str, Any]] | None = None,
    baseline: dict[str, dict[str, Any]] | None = None,
    sample_size: int = SAMPLE_SIZE,
    rejected_inputs: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    n = len(attempts)
    by_outcome = Counter(a.outcome for a in attempts)
    by_platform: dict[str, Counter] = {}
    for a in attempts:
        by_platform.setdefault(a.platform, Counter())[a.outcome] += 1
    ok = by_outcome.get("ok", 0)
    blocked = by_outcome.get("blocked", 0) + by_outcome.get("error", 0)
    eligible = n - by_outcome.get("login-wall", 0) - by_outcome.get("not-found", 0)
    block_rate = (blocked / eligible) if eligible else 1.0
    usable_rate = (ok / eligible) if eligible else 0.0
    fields = Counter(f for a in attempts if a.outcome == "ok" for f in a.fields_filled)
    fill = {k: round(v / ok, 3) for k, v in fields.items()} if ok else {}
    value = incremental_value(records or [], baseline)

    criteria = {
        "sample": {"pass": source == "frontier" and n >= sample_size, "attempted": n, "required": sample_size, "source": source},
        "block_rate": {"pass": eligible > 0 and block_rate <= MAX_BLOCK_RATE, "value": round(block_rate, 3), "max": MAX_BLOCK_RATE, "eligible": eligible},
        "usable_rate": {"pass": eligible > 0 and usable_rate >= MIN_USABLE_RATE, "value": round(usable_rate, 3), "min": MIN_USABLE_RATE},
        "incremental_value": {
            "pass": value["available"] and value["coverage"] >= MIN_BASELINE_COVERAGE and value["mean_fields_added_or_refreshed"] >= MIN_INCREMENTAL_FIELDS,
            "min_mean_fields": MIN_INCREMENTAL_FIELDS,
            "min_coverage": MIN_BASELINE_COVERAGE,
            **value,
        },
    }
    if not value["available"] or source != "frontier":
        decision: Decision = "INCONCLUSIVE"
        reason = "no baseline for incremental value" if not value["available"] else "sample came from an experiment file, not the frontier"
        if not all(c["pass"] for k, c in criteria.items() if k not in ("incremental_value", "sample")):
            decision, reason = "NO-GO", "block/usable criteria failed"
    elif all(c["pass"] for c in criteria.values()):
        decision, reason = "GO", "all criteria met"
    else:
        decision, reason = "NO-GO", "failed: " + ", ".join(k for k, c in criteria.items() if not c["pass"])

    return {
        "attempts": n,
        "eligible_for_block_rate": eligible,
        "by_outcome": dict(by_outcome),
        "by_platform": {k: dict(v) for k, v in by_platform.items()},
        "block_rate": round(block_rate, 3),
        "usable_rate": round(usable_rate, 3),
        "fill_rate_on_ok": fill,
        "fetcher_mix": dict(Counter(a.fetcher for a in attempts)),
        "criteria": criteria,
        "decision": decision,
        "decision_reason": reason,
        "rejected_inputs": [{"url": u, "reason": r} for u, r in (rejected_inputs or [])],
        "attempts_detail": [asdict(a) for a in attempts],
    }
