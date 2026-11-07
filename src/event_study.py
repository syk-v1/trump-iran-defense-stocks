"""Steps 4-5: abnormal returns around posts, and the placebo distribution they are judged by.

Building blocks used by src/run_study.py:

  Prices  "B(t)" = ITA or SPY price at minute boundary t = close of the 1-minute bar that
          ends at t (the bar labelled t - 1 min). Defined for regular-session boundaries
          09:31 ... 16:00 (13:00 on early-close days). ITA minutes with no trade carry the
          last price forward (gaps are at most 6 minutes).
  Return  r = B(t1) / B(t0) - 1.   Abnormal return  AR = r_ITA - beta * r_SPY.
          No intercept: over 30 minutes the market model's alpha is a fraction of a bp.
  Events  Directional Iran posts (escalating / de-escalating). Posts within
          CLUSTER_MINUTES of the first post in a cluster join it. A cluster whose posts
          point in both directions is "mixed" and dropped.
  Regular-hours window: t0 = the post's minute, floored; t1 = t0 + 30 min; both inside
          the regular session (so the post is between 09:31 and 15:30 New York time).
  Pre-window (reverse causality): B(t0 - 30) to B(t0), if t0 - 30 is inside the session.
  Off-hours window ("next open"): from the last regular price before the post to
          B(10:00) of the next session.
  Placebo  every eligible t0 in the same period with no post of any kind (text or
          media) within PLACEBO_CLEAR_MINUTES either side.
"""

from dataclasses import dataclass
from datetime import date, time

import numpy as np
import pandas as pd

from src import config
from src.market_hours import NEW_YORK

WINDOW = pd.Timedelta(minutes=config.EVENT_WINDOW_MIN)
PRE = pd.Timedelta(minutes=config.PRE_WINDOW_MIN)


# ------------------------------------------------------------------ prices

@dataclass
class Prices:
    """Boundary prices for ITA and SPY, plus each trading day's session edges (UTC)."""
    b: pd.DataFrame          # index = boundary (UTC), columns = [ITA, SPY]
    first: pd.Series         # per NY trading day: first boundary (09:31)
    last: pd.Series          # per NY trading day: last boundary (16:00 / 13:00)

    @property
    def days(self) -> list[date]:
        return list(self.first.index)


def load_prices(path=config.PRICES_CSV) -> Prices:
    p = pd.read_csv(path, usecols=["ts_utc", "symbol", "close", "session"])
    p = p[p["session"] == "regular"]
    p["ts_utc"] = pd.to_datetime(p["ts_utc"], utc=True)
    wide = p.pivot(index="ts_utc", columns="symbol", values="close")[[config.TICKER, config.MARKET]]
    day = wide.index.tz_convert(NEW_YORK).date
    frames = []
    for d, g in wide.groupby(day):
        grid = pd.date_range(g.index.min(), g.index.max(), freq="min")   # full minute grid
        frames.append(g.reindex(grid).ffill())
    full = pd.concat(frames)
    full.index = full.index + pd.Timedelta(minutes=1)    # bar start -> boundary at its end
    nyday = pd.Series(full.index.tz_convert(NEW_YORK).date, index=full.index)
    first = pd.Series(full.index, index=full.index).groupby(nyday.values).min()
    last = pd.Series(full.index, index=full.index).groupby(nyday.values).max()
    return Prices(b=full, first=first, last=last)


def ret(prices: Prices, t0: pd.Timestamp, t1: pd.Timestamp) -> tuple[float, float]:
    a, z = prices.b.loc[t0], prices.b.loc[t1]
    r = z / a - 1
    return float(r[config.TICKER]), float(r[config.MARKET])


# ------------------------------------------------------------------ beta

def estimate_beta(prices: Prices, days: list[date], k: int) -> dict:
    """OLS slope of k-minute ITA returns on SPY returns over regular sessions of `days`.
    Returns are non-overlapping and never span the overnight gap."""
    ri, rs = [], []
    for d in days:
        if d not in prices.first.index:
            continue
        g = prices.b.loc[prices.first[d]:prices.last[d]].iloc[::k]
        r = g.pct_change().dropna()
        ri.append(r[config.TICKER].values)
        rs.append(r[config.MARKET].values)
    ri, rs = np.concatenate(ri), np.concatenate(rs)
    beta = np.cov(ri, rs)[0, 1] / np.var(rs, ddof=1)
    resid = ri - beta * rs
    return {"k": k, "beta": beta, "n": len(ri),
            "se": np.sqrt(np.var(resid, ddof=2) / (np.var(rs, ddof=1) * (len(ri) - 1)))}


# ------------------------------------------------------------------ events

def cluster(posts: pd.DataFrame, gap: pd.Timedelta, key=None) -> pd.DataFrame:
    """Group directional posts into events. `posts` needs created_utc, direction, new_info,
    post_id. A post joins the open cluster if it is within `gap` of the cluster's first post
    (or, when `key` is given, if it has the same key, e.g. the same overnight gap)."""
    posts = posts.sort_values("created_utc")
    events, cur = [], None
    for _, p in posts.iterrows():
        same = (cur is not None and
                ((key is None and p.created_utc - cur["created_utc"] < gap) or
                 (key is not None and p[key] == cur["key"])))
        if same:
            cur["dirs"].add(p.direction)
            cur["new"] |= p.new_info == "new"
            cur["post_ids"].append(p.post_id)
        else:
            if cur:
                events.append(cur)
            cur = {"created_utc": p.created_utc, "dirs": {p.direction}, "new": p.new_info == "new",
                   "post_ids": [p.post_id], "key": p[key] if key else None}
    if cur:
        events.append(cur)
    out = pd.DataFrame(events)
    if out.empty:
        return out
    out["direction"] = out["dirs"].map(lambda s: next(iter(s)) if len(s) == 1 else "mixed")
    out["n_posts"] = out["post_ids"].map(len)
    return out.drop(columns=["dirs"])


def regular_window(prices: Prices, s: pd.Timestamp) -> tuple | None:
    """(t0, t1) if a full 30-minute window after `s` fits in that day's session."""
    t0 = s.floor("min")
    d = t0.tz_convert(NEW_YORK).date()
    if d not in prices.first.index:
        return None
    if t0 < prices.first[d] or t0 + WINDOW > prices.last[d]:
        return None
    return t0, t0 + WINDOW


def pre_window(prices: Prices, t0: pd.Timestamp) -> tuple | None:
    d = t0.tz_convert(NEW_YORK).date()
    return (t0 - PRE, t0) if t0 - PRE >= prices.first[d] else None


def offhours_window(prices: Prices, s: pd.Timestamp) -> tuple | None:
    """From the last regular price at or before `s` to 10:00 of the next session after it."""
    t = s.floor("min")
    before = prices.b.index[prices.b.index <= t]
    if len(before) == 0:
        return None
    start = before[-1]
    later = [d for d in prices.days if prices.first[d] > t]
    if not later:
        return None
    end = prices.first[later[0]] + pd.Timedelta(minutes=29)     # 09:31 + 29 = 10:00
    return start, end


def overnight_key(prices: Prices, s: pd.Timestamp):
    w = offhours_window(prices, s)
    return w[1] if w else None


# ------------------------------------------------------------------ placebo

def placebo_starts(prices: Prices, days: list[date], post_times: np.ndarray,
                   clear: pd.Timedelta) -> pd.DatetimeIndex:
    """Every regular-session t0 on `days` with a full window and no post within `clear`."""
    starts = []
    for d in days:
        if d not in prices.first.index:
            continue
        idx = prices.b.loc[prices.first[d]:prices.last[d] - WINDOW].index
        starts.append(idx)
    starts = starts[0].append(starts[1:]) if len(starts) > 1 else starts[0]
    t = starts.as_unit("ns").asi8        # pandas 3 may store us; compare in ns throughout
    lo = np.searchsorted(post_times, t - clear.value, side="left")
    hi = np.searchsorted(post_times, t + clear.value, side="right")
    return starts[hi == lo]


def abnormal(prices: Prices, t0s: pd.DatetimeIndex, t1s: pd.DatetimeIndex, beta: float) -> np.ndarray:
    a = prices.b.loc[t0s].values
    z = prices.b.loc[t1s].values
    r = z / a - 1
    return r[:, 0] - beta * r[:, 1]


def mean_test(observed: float, pool: np.ndarray, n: int, draws: int, rng, side: str) -> float:
    """Share of `draws` means of n placebo ARs at least as extreme as `observed`."""
    sims = rng.choice(pool, size=(draws, n), replace=True).mean(axis=1)
    if side == "greater":
        return float((sims >= observed).mean())
    if side == "less":
        return float((sims <= observed).mean())
    return float((np.abs(sims) >= abs(observed)).mean())


def signed_mean_test(observed: float, pool: np.ndarray, n_esc: int, n_de: int,
                     draws: int, rng) -> float:
    """H1 combined: mean of (+AR for escalating, -AR for de-escalating), vs placebo."""
    esc = rng.choice(pool, size=(draws, n_esc), replace=True)
    de = -rng.choice(pool, size=(draws, n_de), replace=True)
    sims = np.concatenate([esc, de], axis=1).mean(axis=1)
    return float((sims >= observed).mean())
