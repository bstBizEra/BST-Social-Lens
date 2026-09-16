"""Pure-logic tests: frontier eligibility, extraction on fixtures, report/go-no-go.
No network, no browser."""
from __future__ import annotations

from pathlib import Path

from worker.extract import classify_response, extract, to_record
from worker.frontier import eligibility_of, platform_of, select_targets
from worker.report import Attempt, seen_status_for, summarize

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


def _att(outcome: str, platform: str = "tiktok", fields=None) -> Attempt:
    return Attempt(url="u", platform=platform, eligibility="public", fetcher="curl_cffi", status=200,
                   outcome=outcome, seen_status=seen_status_for(outcome), fields_filled=fields or [])


def test_report_go_no_go_excludes_login_walls_from_block_rate():
    ok = [_att("ok", fields=["text", "views_count"]) for _ in range(9)]
    walls = [_att("login-wall", "facebook") for _ in range(20)]
    blocked = [_att("blocked")]
    s = summarize(ok + walls + blocked)
    assert s["eligible_for_block_rate"] == 10
    assert s["block_rate"] == 0.1
    assert s["decision"] == "GO"
    assert s["fill_rate_on_ok"]["text"] == 1.0
    # 5 blocked of 10 eligible → NO-GO
    s2 = summarize(ok[:5] + [_att("blocked")] * 5)
    assert s2["decision"] == "NO-GO" and s2["block_rate"] == 0.5
    # too small a sample → NO-GO regardless
    assert summarize(ok[:3])["decision"] == "NO-GO"
