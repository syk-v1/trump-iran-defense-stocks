"""Tests for src/market_hours.py::market_session (your TODOs)."""

import pandas as pd
import pytest

from src.market_hours import market_session


def utc(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


@pytest.mark.parametrize("ts, expected, why", [
    # Summer (EDT, New York = UTC-4)
    (utc("2025-06-16 14:00"), "regular", "Mon 10:00 EDT"),
    (utc("2025-06-16 13:29"), "pre", "Mon 09:29 EDT"),
    (utc("2025-06-16 13:30"), "regular", "09:30:00 exactly opens the session"),
    (utc("2025-06-16 20:00"), "post", "16:00:00 exactly is after the close"),
    (utc("2025-06-16 23:59"), "post", "19:59 EDT"),
    (utc("2025-06-17 00:00"), "closed", "20:00 EDT, after-hours over"),
    (utc("2025-06-17 07:59"), "closed", "03:59 EDT, before pre-market"),
    (utc("2025-06-17 08:00"), "pre", "04:00 EDT, pre-market opens"),
    # Weekends and holidays
    (utc("2025-06-21 23:50"), "closed", "Sat 19:50 EDT (US strikes announced)"),
    (utc("2025-06-19 15:00"), "closed", "Thu 11:00 EDT but Juneteenth, NYSE shut"),
    (utc("2026-04-03 15:00"), "closed", "Good Friday 2026"),
    # A UTC Monday that is still Sunday evening in New York
    (utc("2025-06-23 02:00"), "closed", "Sun 22:00 EDT"),
    # Early close
    (utc("2025-07-03 16:59"), "regular", "12:59 EDT on an early-close day"),
    (utc("2025-07-03 17:30"), "post", "13:30 EDT on an early-close day"),
    # Winter (EST, New York = UTC-5): the same UTC clock time means something else
    (utc("2026-03-05 14:00"), "pre", "Thu 09:00 EST, not open yet"),
    (utc("2026-03-05 14:30"), "regular", "09:30 EST"),
    # The week clocks changed (DST began Sun 8 March 2026)
    (utc("2026-03-09 13:50"), "regular", "Mon 09:50 EDT, the first day after DST"),
    (utc("2026-03-06 13:50"), "pre", "Fri 08:50 EST, the last day before DST"),
])
def test_market_session(ts, expected, why):
    assert market_session(ts) == expected, why


def test_accepts_other_timezones():
    # The same instant written in London time must give the same answer.
    ts = pd.Timestamp("2025-06-16 15:00", tz="Europe/London")   # = 10:00 EDT
    assert market_session(ts) == "regular"


def test_naive_timestamp_rejected():
    with pytest.raises(ValueError):
        market_session(pd.Timestamp("2025-06-16 14:00"))
