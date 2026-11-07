"""Tests for the event-study building blocks, on a tiny synthetic price set."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from src import event_study as es
from src.agreement import cohen_kappa, rule_label
from src.candidates import IRAN_PATTERN, resolve_links


def make_prices(days=(date(2025, 6, 16), date(2025, 6, 17))) -> es.Prices:
    """Two summer days; ITA rises 1 bp a minute, SPY is flat, except a 10 bp SPY jump at 10:30."""
    frames = []
    for d in days:
        grid = pd.date_range(pd.Timestamp(f"{d} 09:31", tz="America/New_York"),
                             pd.Timestamp(f"{d} 16:00", tz="America/New_York"), freq="min").tz_convert("UTC")
        ita = 100 * (1.0001 ** np.arange(len(grid)))
        spy = np.where(grid >= pd.Timestamp(f"{d} 10:30", tz="America/New_York"), 101.0, 100.0)
        frames.append(pd.DataFrame({"ITA": ita, "SPY": spy}, index=grid))
    b = pd.concat(frames)
    nyday = b.index.tz_convert("America/New_York").date
    s = pd.Series(b.index, index=b.index)
    return es.Prices(b=b, first=s.groupby(nyday).min(), last=s.groupby(nyday).max())


def ny(s):
    return pd.Timestamp(s, tz="America/New_York").tz_convert("UTC")


def test_regular_window_needs_full_session():
    P = make_prices()
    assert es.regular_window(P, ny("2025-06-16 10:15:30")) == (ny("2025-06-16 10:15"), ny("2025-06-16 10:45"))
    assert es.regular_window(P, ny("2025-06-16 15:30:59")) is not None     # ends exactly 16:00
    assert es.regular_window(P, ny("2025-06-16 15:31:00")) is None
    assert es.regular_window(P, ny("2025-06-16 09:30:40")) is None         # needs the 09:30 bar's close
    assert es.regular_window(P, ny("2025-06-14 12:00")) is None            # Saturday


def test_pre_window_only_inside_session():
    P = make_prices()
    assert es.pre_window(P, ny("2025-06-16 10:15")) == (ny("2025-06-16 09:45"), ny("2025-06-16 10:15"))
    assert es.pre_window(P, ny("2025-06-16 09:45")) is None


def test_offhours_window_runs_to_next_10am():
    P = make_prices()
    assert es.offhours_window(P, ny("2025-06-16 19:00")) == (ny("2025-06-16 16:00"), ny("2025-06-17 10:00"))
    assert es.offhours_window(P, ny("2025-06-17 08:00")) == (ny("2025-06-16 16:00"), ny("2025-06-17 10:00"))


def test_abnormal_return_removes_market_move():
    P = make_prices()
    t0, t1 = pd.DatetimeIndex([ny("2025-06-16 10:15")]), pd.DatetimeIndex([ny("2025-06-16 10:45")])
    ar = es.abnormal(P, t0, t1, beta=0.5)[0]
    r_ita = 1.0001 ** 30 - 1
    assert ar == pytest.approx(r_ita - 0.5 * 0.01)


def test_cluster_merges_within_gap_and_flags_mixed():
    posts = pd.DataFrame({
        "post_id": [1, 2, 3, 4],
        "created_utc": [ny("2025-06-16 10:00"), ny("2025-06-16 10:20"), ny("2025-06-16 11:00"), ny("2025-06-16 11:10")],
        "direction": ["escalating", "escalating", "escalating", "de-escalating"],
        "new_info": ["repeat", "new", "new", "new"],
    })
    ev = es.cluster(posts, pd.Timedelta(minutes=30))
    assert ev["post_ids"].tolist() == [[1, 2], [3, 4]]
    assert ev["direction"].tolist() == ["escalating", "mixed"]
    assert ev["new"].tolist() == [True, True]


def test_placebo_excludes_minutes_near_any_post():
    P = make_prices()
    post = np.array([ny("2025-06-16 12:00").as_unit("ns").value])
    starts = es.placebo_starts(P, [date(2025, 6, 16)], post, pd.Timedelta(minutes=30))
    assert ny("2025-06-16 11:29") in starts
    assert ny("2025-06-16 11:30") not in starts and ny("2025-06-16 12:30") not in starts
    assert ny("2025-06-16 12:31") in starts
    assert starts.max() == ny("2025-06-16 15:30")


def test_beta_recovers_known_slope():
    P = make_prices()
    rng = np.random.default_rng(0)
    spy = 100 * np.cumprod(1 + rng.normal(0, 1e-4, len(P.b)))
    P.b["SPY"] = spy
    P.b["ITA"] = 100 * np.cumprod(1 + 0.8 * np.r_[0, np.diff(spy) / spy[:-1]])
    assert es.estimate_beta(P, P.days, 5)["beta"] == pytest.approx(0.8, abs=0.02)


def test_kappa():
    a = pd.Series(["x", "x", "y", "y"])
    assert cohen_kappa(a, a) == 1
    assert cohen_kappa(a, pd.Series(["x", "y", "x", "y"])) == pytest.approx(0)


def test_rule_coder():
    assert rule_label("We will hit Iran very hard tonight") == ("yes", "escalating")
    assert rule_label("Ceasefire with Iran agreed, peace talks continue") == ("yes", "de-escalating")
    assert rule_label("Vote for my candidate") == ("no", "")


def test_candidate_pattern_catches_calibration_misses():
    for text in ["CONGRATULATIONS WORLD, IT'S TIME FOR PEACE!", "attack today at the American Base in Qatar",
                 "KEEP OIL PRICES DOWN", "Khamenei is dead"]:
        assert IRAN_PATTERN.search(text), text
    assert not IRAN_PATTERN.search("The One Big Beautiful Bill passed the Senate")


def test_resolve_links_to_own_posts():
    posts = pd.DataFrame({"truth_id": ["111", "222"], "quoted_text": ["", ""],
                          "text": ["Peace with Iran!", "https://truthsocial.com/@realDonaldTrump/111"]})
    assert resolve_links(posts).tolist() == ["", "Peace with Iran!"]
