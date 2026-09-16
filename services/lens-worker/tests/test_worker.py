"""Pure-logic tests: frontier eligibility, extraction on fixtures, report/go-no-go.
No network, no browser."""
from __future__ import annotations

from pathlib import Path

from worker.extract import classify_response, extract, to_record
from worker.frontier import TargetRejected, eligibility_of, platform_of, select_targets, targets_from_experiment_file, validate_target
import pytest

from worker.report import Attempt, incremental_value, seen_status_for, summarize

FX = Path(__file__).parent / "fixtures"
fx = lambda n: (FX / n).read_text(encoding="utf-8")  # noqa: E731


def test_eligibility_classification():
    assert eligibility_of("https://www.tiktok.com/@laoland/video/7412345678901234567") == "public"
    assert eligibility_of("https://www.tiktok.com/@laoland") == "unsupported"
    assert eligibility_of("https://www.facebook.com/groups/123/posts/456/") == "login-likely"
    assert eligibility_of("https://www.facebook.com/laopropertyhub/posts/pfbid0abc") == "public"
    assert eligibility_of("https://www.facebook.com/story.php?id=1&story_fbid=2") == "public"
    assert eligibility_of("https://www.facebook.com/groups/123/") == "unsupported"
    assert platform_of("https://m.facebook.com/x") == "facebook"


def test_select_targets_only_seen_and_supported():
    links = [
        {"url_hash": "a", "url": "https://www.tiktok.com/@x/video/1", "last_status": "seen"},
        {"url_hash": "b", "url": "https://www.tiktok.com/@x/video/2", "last_status": "fetched"},
        {"url_hash": "c", "url": "https://www.facebook.com/groups/1/posts/2/", "last_status": "seen"},
        {"url_hash": "d", "url": "https://www.facebook.com/groups/1/", "last_status": "seen"},
    ]
    t = select_targets(links, 10)
    assert [x.url_hash for x in t] == ["a", "c"]
    assert [x.url_hash for x in select_targets(links, 10, include_login_likely=False)] == ["a"]
    assert len(select_targets(links, 1)) == 1


def test_tiktok_public_extraction():
    x = extract("tiktok", 200, fx("tiktok-video-public.html"), "https://www.tiktok.com/@laoland/video/7412345678901234567")
    assert x.outcome == "ok"
    assert x.post_id == "7412345678901234567"
    assert x.views_count == 45000 and x.reactions_total == 1200 and x.comments_count == 88 and x.shares_count == 15
    assert x.author_name == "Lao Land" and x.author_url == "https://www.tiktok.com/@laoland"
    assert x.hashtags == ["ຂາຍດິນ", "vientiane"]
    assert x.lang == "lo" and x.created_at == "2026-09-15T19:20:00Z"
    rec = to_record(x, "2026-09-16T10:00:00Z", "worker-0.1.0")
    assert rec and rec["key"] == "tiktok:7412345678901234567" and rec["record_type"] == "post"
    assert "author_id" not in rec and "author_hash" not in rec  # nothing to hash logged-out


def test_tiktok_login_wall_is_final():
    x = extract("tiktok", 200, fx("tiktok-video-loginwall.html"), "https://www.tiktok.com/@x/video/1")
    assert x.outcome == "login-wall"
    assert seen_status_for(x.outcome) == "skipped"
    assert to_record(x, "2026-09-16T10:00:00Z", "w") is None


def test_facebook_public_page_post():
    x = extract("facebook", 200, fx("facebook-page-post-public.html"), "https://www.facebook.com/laopropertyhub/posts/1234567890")
    assert x.outcome == "ok" and x.post_id == "1234567890"
    assert x.reactions_total == 57 and x.comments_count == 12 and x.shares_count == 4
    assert x.author_name == "Lao Property Hub"
    assert x.media == [{"kind": "image", "url": "https://scontent.example/house.jpg"}]
    assert "ຂາຍບ້ານ" in x.hashtags
    assert x.created_at == "2026-09-14T15:33:20Z"


def test_facebook_login_wall_and_blocks():
    assert classify_response(200, fx("facebook-login-wall.html")) == "login-wall"
    assert classify_response(403, "") == "blocked"
    assert classify_response(429, "x") == "blocked"
    assert classify_response(200, "<html>Please wait while we verify your browser</html>") == "blocked"
    assert classify_response(404, "") == "not-found"


def test_acquisition_boundary():
    ok = "https://www.tiktok.com/@laoland/video/7412345678901234567"
    assert validate_target(ok) == ok
    for bad, why in [
        ("http://www.tiktok.com/@x/video/1", "https"),
        ("https://127.0.0.1/@x/video/1", "IP"),
        ("https://localhost/@x/video/1", "localhost"),
        ("https://10.0.0.5/groups/1/posts/2/", "IP"),
        ("https://[::1]/@x/video/1", "IP"),
        ("https://evil.example/@x/video/1", "allow-list"),
        ("https://facebook.com.evil.example/x/posts/1", "allow-list"),
        ("https://user:pw@www.facebook.com/x/posts/1", "credentials"),
        ("https://www.facebook.com:8443/x/posts/1", "port"),
        ("https://www.facebook.com/groups/123/", "permalink"),
        ("https://www.tiktok.com/@laoland", "permalink"),
    ]:
        with pytest.raises(TargetRejected, match=why):
            validate_target(bad)


def test_experiment_file_applies_boundary_and_reports_rejections():
    lines = ["# comment", "", "https://www.tiktok.com/@x/video/1", "http://www.tiktok.com/@x/video/2", "https://192.168.1.1/x/posts/1"]
    accepted, rejected = targets_from_experiment_file(lines, 10)
    assert [t.url for t in accepted] == ["https://www.tiktok.com/@x/video/1"]
    assert [r[0] for r in rejected] == ["http://www.tiktok.com/@x/video/2", "https://192.168.1.1/x/posts/1"]


def test_select_targets_drops_boundary_violations_from_frontier():
    links = [{"url_hash": "a", "url": "https://10.1.1.1/@x/video/1", "last_status": "seen"},
             {"url_hash": "b", "url": "https://www.tiktok.com/@x/video/2", "last_status": "seen"}]
    assert [t.url_hash for t in select_targets(links, 10)] == ["b"]


def _att(outcome: str, platform: str = "tiktok", fields=None, key=None) -> Attempt:
    return Attempt(url="u", platform=platform, eligibility="public", fetcher="curl_cffi", status=200,
                   outcome=outcome, seen_status=seen_status_for(outcome), fields_filled=fields or [], record_key=key)


def _rec(i: int, **kw):
    return {"key": f"tiktok:{i}", "platform": "tiktok", "post_id": str(i), "text": "t", "views_count": 100 + i, "comments_count": 5, **kw}


def _baseline(n: int, stale: bool = True):
    # Extension already holds these posts but without views_count; comments_count differs when stale.
    return {f"tiktok:{i}": {"key": f"tiktok:{i}", "text": "t", "comments_count": 3 if stale else 5} for i in range(n)}


def test_gate_go_requires_all_criteria():
    ok = [_att("ok", fields=["text", "views_count"], key=f"tiktok:{i}") for i in range(40)]
    walls = [_att("login-wall", "facebook") for _ in range(8)]
    blocked = [_att("blocked") for _ in range(2)]
    recs = [_rec(i) for i in range(40)]
    s = summarize(ok + walls + blocked, source="frontier", records=recs, baseline=_baseline(40))
    assert s["attempts"] == 50 and s["eligible_for_block_rate"] == 42
    assert s["criteria"]["block_rate"]["value"] == round(2 / 42, 3)
    assert s["criteria"]["usable_rate"]["value"] == round(40 / 42, 3)
    assert s["criteria"]["incremental_value"]["mean_fields_added_or_refreshed"] == 2.0  # views added + comments refreshed
    assert s["decision"] == "GO"


def test_gate_one_ok_forty_nine_empty_is_not_go():
    s = summarize([_att("ok", fields=["text"], key="tiktok:0")] + [_att("empty")] * 49,
                  source="frontier", records=[_rec(0)], baseline=_baseline(1))
    assert s["block_rate"] == 0.0            # nothing "blocked" …
    assert s["decision"] == "NO-GO"          # … but usable rate 2 % fails the gate
    assert "usable_rate" in s["decision_reason"]


def test_gate_sample_size_and_source():
    ok = [_att("ok", fields=["text"], key=f"tiktok:{i}") for i in range(20)]
    recs = [_rec(i) for i in range(20)]
    assert summarize(ok, source="frontier", records=recs, baseline=_baseline(20))["decision"] == "NO-GO"  # 20 < 50
    s = summarize(ok * 3, source="experiment-file", records=recs, baseline=_baseline(20))
    assert s["decision"] == "INCONCLUSIVE" and "experiment file" in s["decision_reason"]


def test_gate_no_baseline_or_no_value_never_go():
    ok = [_att("ok", fields=["text"], key=f"tiktok:{i}") for i in range(50)]
    recs = [_rec(i) for i in range(50)]
    assert summarize(ok, source="frontier", records=recs, baseline=None)["decision"] == "INCONCLUSIVE"
    # Baseline already has everything the worker found → zero incremental value → NO-GO
    full = {f"tiktok:{i}": _rec(i) for i in range(50)}
    s = summarize(ok, source="frontier", records=recs, baseline=full)
    assert s["decision"] == "NO-GO" and "incremental_value" in s["decision_reason"]
    # Baseline covers too few OK records → coverage fails → NO-GO
    s2 = summarize(ok, source="frontier", records=recs, baseline=_baseline(10))
    assert s2["criteria"]["incremental_value"]["coverage"] == 0.2 and s2["decision"] == "NO-GO"


def test_incremental_value_counts_added_and_refreshed():
    v = incremental_value([_rec(1)], {"tiktok:1": {"key": "tiktok:1", "text": "t", "comments_count": 5}})
    assert v["compared"] == 1 and v["per_field"] == {"added:views_count": 1}
