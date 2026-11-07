"""Project-wide settings: episode dates, tickers, paths, and the NYSE calendar.

Everything that is a research *decision* (dates, window lengths) lives here so it is
written down once, before looking at results, and every script reads the same values.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_POSTS_DIR = DATA_DIR / "raw" / "posts"        # one cached RSS file per UTC day
PROCESSED_DIR = DATA_DIR / "processed"
POSTS_CSV = PROCESSED_DIR / "posts.csv"            # posts with text: the study's sample
POST_TIMES_CSV = PROCESSED_DIR / "post_times.csv"  # every post's time, for the placebo step
RAW_PRICES_DIR = DATA_DIR / "raw" / "prices"      # one cached JSON file per trading day
PRICES_CSV = PROCESSED_DIR / "prices_1min.csv"
CANDIDATES_CSV = PROCESSED_DIR / "candidates.csv"   # posts that might be about Iran
LABELS_DIR = ROOT / "labels"                        # hand-made, committed to git
LABELS_CSV = LABELS_DIR / "labels.csv"
RESULTS_DIR = ROOT / "results"

TICKER = "ITA"      # iShares U.S. Aerospace & Defense ETF
MARKET = "SPY"      # market proxy for the market model

# Alpaca market data. "sip" = consolidated tape (every US exchange); "iex" = one exchange,
# a few % of volume, too thin for minute bars on ITA. Prices are adjusted for splits and
# dividends ("all") so that overnight returns across ex-dividend dates aren't distorted.
ALPACA_FEED = "sip"
ALPACA_ADJUSTMENT = "all"

EVENT_WINDOW_MIN = 30   # abnormal return measured over the 30 minutes after a post
PRE_WINDOW_MIN = 30     # ...and the 30 minutes before it (reverse-causality check)

# ---- Frozen after calibration on the Twelve-Day War (docs/decisions.md, step 6) ----
BETA_RETURN_MIN = 5         # beta from 5-minute returns (1-minute returns bias it down)
BETA_ROBUST_MIN = 30        # robustness check only
CLUSTER_MINUTES = 30        # posts within 30 min of a cluster's first post join it
PLACEBO_CLEAR_MINUTES = 30  # placebo t0 has no post of any kind within +-30 min
PLACEBO_DRAWS = 10_000
SEED = 42


@dataclass(frozen=True)
class Episode:
    name: str
    role: str                       # "development" or "test"
    start: date                     # first day of the conflict
    end: date | None                # last day; None = still open, collect up to yesterday
    phase_split: date | None = None  # H2: first day of the later phase


EPISODES = {
    "twelve_day_war": Episode(
        name="twelve_day_war",
        role="development",
        start=date(2025, 6, 13),    # Israeli strikes began early 13 June (Tehran time)
        end=date(2025, 6, 24),      # ceasefire took hold 24 June
    ),
    "iran_war_2026": Episode(
        name="iran_war_2026",
        role="test",
        start=date(2026, 2, 28),
        end=date(2026, 6, 16),      # day before the Islamabad MoU (17 June); see docs/decisions.md
        phase_split=date(2026, 4, 8),  # two-week ceasefire; H2 compares before vs from here
    ),
}

# Posts are collected for this many days either side of each episode, so later steps
# have post-free times for the placebo distribution and a pre-event window for beta.
PAD_DAYS = 30

# ------------------------------------------------------------------ NYSE calendar
# Full-day closures and 13:00 early closes, from NYSE's published holiday calendar.
# The 1-minute price data is the final word on which minutes traded; these lists are
# for classifying post times before prices are loaded.
NYSE_HOLIDAYS = {
    # 2025
    date(2025, 1, 1), date(2025, 1, 9),   # 9 Jan: national day of mourning (Carter)
    date(2025, 1, 20), date(2025, 2, 17), date(2025, 4, 18), date(2025, 5, 26),
    date(2025, 6, 19), date(2025, 7, 4), date(2025, 9, 1), date(2025, 11, 27),
    date(2025, 12, 25),
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
}
NYSE_EARLY_CLOSES = {
    date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24),
    date(2026, 11, 27), date(2026, 12, 24),
}
