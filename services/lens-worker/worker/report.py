"""Block-rate report + go/no-go rule (ADR-0004 rule 5) — pure."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

GO_MAX_BLOCK_RATE = 0.20  # ≤ 20 % blocked on the sample → GO


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


def seen_status_for(outcome: str) -> str:
    if outcome == "ok":
        return "fetched"
    if outcome in ("login-wall", "not-found", "empty"):
        return "skipped"  # final: do not retry with a session (rule 3)
    return "failed"  # blocked / error → may retry later


def summarize(attempts: list[Attempt]) -> dict[str, Any]:
    n = len(attempts)
    by_outcome = Counter(a.outcome for a in attempts)
    by_platform: dict[str, Counter] = {}
    for a in attempts:
        by_platform.setdefault(a.platform, Counter())[a.outcome] += 1
    # "Blocked" for go/no-go = bot-detection or network refusal, NOT login walls
    # (a private group is not a detection failure; it is out of Layer C scope by design).
    blocked = by_outcome.get("blocked", 0) + by_outcome.get("error", 0)
    eligible = n - by_outcome.get("login-wall", 0) - by_outcome.get("not-found", 0)
    block_rate = (blocked / eligible) if eligible else 0.0
    fields = Counter(f for a in attempts if a.outcome == "ok" for f in a.fields_filled)
    ok = by_outcome.get("ok", 0)
    fill = {k: round(v / ok, 3) for k, v in fields.items()} if ok else {}
    decision = "GO" if (eligible >= 10 and block_rate <= GO_MAX_BLOCK_RATE and ok > 0) else "NO-GO"
    return {
        "attempts": n,
        "eligible_for_block_rate": eligible,
        "by_outcome": dict(by_outcome),
        "by_platform": {k: dict(v) for k, v in by_platform.items()},
        "block_rate": round(block_rate, 3),
        "go_threshold": GO_MAX_BLOCK_RATE,
        "fill_rate_on_ok": fill,
        "fetcher_mix": dict(Counter(a.fetcher for a in attempts)),
        "decision": decision,
        "attempts_detail": [asdict(a) for a in attempts],
    }
