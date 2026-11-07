"""Tests for src/collect_posts.py. Offline: they use tests/fixtures/feed_sample.xml."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src import config
from src.collect_posts import (COLUMNS, FEED_LIMIT, build_table, check_not_truncated,
                               collection_days, has_text, html_to_text, parse_feed,
                               time_from_truth_id)

FIXTURE = Path(__file__).parent / "fixtures" / "feed_sample.xml"


@pytest.fixture
def rows():
    return {r["post_id"]: r for r in parse_feed(FIXTURE.read_bytes())}


def test_parses_every_item(rows):
    assert sorted(rows) == [1001, 1002, 1003, 1004]
    assert set(rows[1001]) == set(COLUMNS) - {"episode"}


def test_times_are_utc(rows):
    assert rows[1001]["created_utc"] == pd.Timestamp("2025-06-21 23:50:59", tz="UTC")
    assert str(rows[1001]["created_utc"].tz) == "UTC"


def test_id_time_matches_pubdate(rows):
    for r in rows.values():
        gap = abs((r["created_utc"] - r["created_utc_from_id"]).total_seconds())
        assert gap < 1, r["post_id"]


def test_time_from_truth_id_keeps_milliseconds():
    assert time_from_truth_id("114724035567288320") == pd.Timestamp("2025-06-21 23:50:59.120", tz="UTC")
    assert time_from_truth_id("") is None


def test_text_cleaning(rows):
    assert rows[1001]["text"] == "First line of a test post & more. Second line.\nAnother paragraph."
    assert rows[1004]["text"] == 'Plain post with "quotes" and extra spaces.'


def test_quote_post(rows):
    q = rows[1002]
    assert q["post_type"] == "quote"
    assert q["text"] == "My comment on the quoted post."       # no "Quoted Some Account"
    assert q["quoted_text"] == "The quoted words."            # no "View video"
    assert q["ref_type"] == "quote"
    assert q["ref_author"] == "Some Account"
    assert q["ref_truth_id"] == "114724035567288320"
    assert q["ref_created_utc"] == pd.Timestamp("2025-06-21 23:50:59", tz="UTC")
    assert q["media_types"] == "image;video"


def test_removed_repost(rows):
    r = rows[1003]
    assert r["post_type"] == "repost"
    assert r["text"] == ""                       # reposted words are kept once, below
    assert r["quoted_text"] == "Reposted words."
    assert r["ref_author"] == "Donald J. Trump"
    assert r["media_types"] == "image"           # media inside <archive:reference>
    assert r["removed"] is True
    assert r["removal_detected_utc"] == pd.Timestamp("2026-04-02 00:43:22", tz="UTC")
    assert rows[1001]["removed"] is False
    assert rows[1001]["removal_detected_utc"] is None


def test_paragraph_starting_with_quoted_is_kept():
    own, quoted = html_to_text("<p>Quoted in the press today: we are winning.</p>")
    assert own == "Quoted in the press today: we are winning."
    assert quoted == ""


def test_truncated_day_is_an_error():
    check_not_truncated(b"<item>" * (FEED_LIMIT - 1), date(2026, 3, 1))
    with pytest.raises(RuntimeError, match="maximum"):
        check_not_truncated(b"<item>" * FEED_LIMIT, date(2026, 3, 1))


def test_collection_days_closed_episode():
    ep = config.Episode("x", "development", date(2025, 6, 13), date(2025, 6, 24))
    days = collection_days(ep, pad_days=30, today=date(2026, 11, 7))
    assert days[0] == date(2025, 5, 14)
    assert days[-1] == date(2025, 7, 24)
    assert len(days) == 72                       # no gaps, no repeats


def test_collection_days_open_episode_stops_yesterday():
    ep = config.Episode("x", "test", date(2026, 2, 28), None)
    days = collection_days(ep, pad_days=30, today=date(2026, 11, 7))
    assert days[0] == date(2026, 1, 29)
    assert days[-1] == date(2026, 11, 6)


def test_has_text_drops_media_only_posts():
    posts = pd.DataFrame({"text": ["words", "", "", None],
                          "quoted_text": ["", "quoted words", "  ", None]})
    assert has_text(posts).tolist() == [True, True, False, False]


def test_build_table_dedupes_and_sorts(tmp_path):
    a = tmp_path / "a.xml"
    a.write_bytes(FIXTURE.read_bytes())
    posts = build_table([a, a], today=date(2026, 11, 7))   # same file twice
    assert list(posts.columns) == COLUMNS
    assert posts["post_id"].is_unique and len(posts) == 4
    assert posts["created_utc"].is_monotonic_increasing
    assert posts.set_index("post_id").loc[1001, "episode"] == "twelve_day_war"
    assert posts.set_index("post_id").loc[1004, "episode"] == "iran_war_2026"
