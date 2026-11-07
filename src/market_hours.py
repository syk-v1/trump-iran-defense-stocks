"""When did a post land, relative to the US stock market's trading day?

This decides which posts can have a 30-minute event window inside regular trading
hours, and which ones need the "drop vs next open" rule.
Tests:   .venv\\Scripts\\python -m pytest tests/test_market_hours.py -v
"""

from datetime import time

import pandas as pd

from src import config

NEW_YORK = "America/New_York"

PRE_OPEN = time(4, 0)        # pre-market starts
OPEN = time(9, 30)           # regular session starts
CLOSE = time(16, 0)          # regular session ends (normal days)
EARLY_CLOSE = time(13, 0)    # regular session ends (days in config.NYSE_EARLY_CLOSES)
POST_CLOSE = time(20, 0)     # after-hours ends


def market_session(ts: pd.Timestamp) -> str:
    """Which part of the US equity trading day the moment `ts` falls in.

    Returns one of:
        "regular"  09:30 <= New York time < close  (close is 16:00, or 13:00 on early-close days)
        "pre"      04:00 <= New York time < 09:30
        "post"     close <= New York time < 20:00
        "closed"   any other time, and all day on weekends and NYSE holidays

    Each range includes its start and excludes its end, so 09:30:00 exactly is
    "regular" and 16:00:00 exactly is "post".

    `ts` must be timezone-aware (posts.csv stores UTC). A timestamp with no timezone
    is ambiguous, so it raises ValueError rather than guessing.
    """
    if ts.tzinfo is None:
        raise ValueError(f"{ts} has no timezone; localise it first, e.g. ts.tz_localize('UTC')")

    # Convert with the tz database, not a fixed offset: New York is UTC-5 in winter
    # and UTC-4 in summer, and the clocks changed on 8 March 2026, inside the test set.
    ny = ts.tz_convert(NEW_YORK)
    day, t = ny.date(), ny.time()

    if ny.weekday() >= 5 or day in config.NYSE_HOLIDAYS:
        return "closed"
    close = EARLY_CLOSE if day in config.NYSE_EARLY_CLOSES else CLOSE

    # Checked in time order, so each test only needs the upper bound.
    if t < PRE_OPEN:
        return "closed"
    if t < OPEN:
        return "pre"
    if t < close:
        return "regular"
    if t < POST_CLOSE:
        return "post"
    return "closed"


def add_session_column(posts: pd.DataFrame, col: str = "created_utc") -> pd.DataFrame:
    """Return a copy of `posts` with a "session" column from market_session."""
    out = posts.copy()
    out["session"] = out[col].map(market_session)
    return out
