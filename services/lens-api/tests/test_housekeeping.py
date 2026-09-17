"""SLL-DATA-HK-001 pure helpers (the live reconciliation runs in test_resolution_live / test_extract_live)."""
from datetime import datetime, timedelta, timezone

from app.housekeeping import HK_VERSION, classify_health, lifecycle_state


def test_health_thresholds():
    assert classify_health(None) == "unknown"
    assert classify_health(1.0) == "healthy" and classify_health(0.99) == "healthy"
    assert classify_health(0.95) == "degraded" and classify_health(0.9) == "degraded"
    assert classify_health(0.5) == "failing" and classify_health(0.0) == "failing"


def test_lifecycle_policy_30_90_180():
    now = datetime(2026, 9, 18, tzinfo=timezone.utc)
    d = lambda days: now - timedelta(days=days)  # noqa: E731
    assert lifecycle_state(None, now) == "UNKNOWN"
    assert lifecycle_state(d(0), now) == "CURRENT" and lifecycle_state(d(30), now) == "CURRENT"
    assert lifecycle_state(d(31), now) == "AGING" and lifecycle_state(d(90), now) == "AGING"
    assert lifecycle_state(d(91), now) == "STALE" and lifecycle_state(d(180), now) == "STALE"
    assert lifecycle_state(d(181), now) == "HISTORICAL"
    assert HK_VERSION == "0.1.0"
