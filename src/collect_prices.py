"""Step 2: download 1-minute bars for ITA and SPY over each episode's collection window.

Usage:
    python -m src.collect_prices
    python -m src.collect_prices --episode twelve_day_war
    python -m src.collect_prices --refresh          # re-download days already cached

Needs a free Alpaca account (alpaca.markets) and its market-data keys in a .env file
in the project root (copy .env.example):
    ALPACA_API_KEY_ID=...
    ALPACA_API_SECRET_KEY=...

What it does:
  * One request per weekday (both tickers together), 04:00 to 20:00 New York time,
    so pre-market and after-hours bars are kept for the "next open" option.
  * Weekdays that are NYSE holidays are requested too: they should come back empty,
    and the summary says so if they don't.
  * Alpaca sends no bar for a minute with no trades. Missing minutes are NOT filled
    here; the summary reports how many regular-session minutes each ticker is missing.

Outputs:
    data/raw/prices/YYYY-MM-DD.json   the API's bars for that day, as received
    data/processed/prices_1min.csv    one row per (minute, ticker):
        ts_utc (bar start), symbol, open, high, low, close, volume, trades, vwap, session
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from src import config
from src.collect_posts import collection_days
from src.market_hours import NEW_YORK, PRE_OPEN, POST_CLOSE, market_session

BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
PAGE_LIMIT = 10_000
SYMBOLS = [config.TICKER, config.MARKET]
REGULAR_MINUTES = 390        # 09:30 to 16:00
EARLY_CLOSE_MINUTES = 210    # 09:30 to 13:00

COLUMNS = ["ts_utc", "symbol", "open", "high", "low", "close", "volume", "trades", "vwap", "session"]
_RENAME = {"t": "ts_utc", "o": "open", "h": "high", "l": "low", "c": "close",
           "v": "volume", "n": "trades", "vw": "vwap"}


# ------------------------------------------------------------------ keys

def load_keys() -> dict[str, str]:
    """Alpaca keys from the environment, or from the .env file in the project root."""
    names = ("ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY")
    found = {n: os.environ.get(n, "") for n in names}
    env_file = config.ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() in names and not found[name.strip()]:
                found[name.strip()] = value.strip().strip('"').strip("'")
    missing = [n for n, v in found.items() if not v or v.startswith("paste_")]
    if missing:
        raise RuntimeError(f"Missing {', '.join(missing)}. Copy .env.example to .env and fill it in.")
    return {"APCA-API-KEY-ID": found[names[0]], "APCA-API-SECRET-KEY": found[names[1]]}


# ------------------------------------------------------------------ which days

def weekdays(days: list[date]) -> list[date]:
    return [d for d in days if d.weekday() < 5]


def expected_trading_days(days: list[date]) -> list[date]:
    return [d for d in weekdays(days) if d not in config.NYSE_HOLIDAYS]


def day_bounds(day: date) -> tuple[str, str]:
    """(start, end) of the extended session on `day`, as UTC RFC-3339 strings.
    End is 19:59 because Alpaca's `end` is inclusive and bars are labelled by their start."""
    start = pd.Timestamp(datetime.combine(day, PRE_OPEN), tz=NEW_YORK)
    end = pd.Timestamp(datetime.combine(day, POST_CLOSE), tz=NEW_YORK) - pd.Timedelta(minutes=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start.tz_convert("UTC").strftime(fmt), end.tz_convert("UTC").strftime(fmt)


# ------------------------------------------------------------------ downloading

def _get(session: requests.Session, params: dict, day: date, max_tries: int = 5) -> dict:
    wait = 5.0
    problem = ""
    for attempt in range(1, max_tries + 1):
        try:
            r = session.get(BARS_URL, params=params, timeout=60)
        except requests.RequestException as exc:
            problem = f"{type(exc).__name__}: {exc}"
        else:
            if r.status_code == 200:
                return r.json()
            if r.status_code in (401, 403):
                raise RuntimeError(f"Alpaca refused the request ({r.status_code}): {r.text[:300]}")
            if r.status_code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"Alpaca error {r.status_code} on {day}: {r.text[:300]}")
            problem = f"HTTP {r.status_code}"
        if attempt < max_tries:
            print(f"  {day}: {problem}, retrying in {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
            wait *= 2
    raise RuntimeError(f"gave up on {day} after {max_tries} tries ({problem})")


def fetch_day(session: requests.Session, day: date) -> dict[str, list[dict]]:
    """All 1-minute bars for SYMBOLS on `day`, following pagination.
    Returns {symbol: [bar, ...]} in Alpaca's own format."""
    start, end = day_bounds(day)
    params = {
        "symbols": ",".join(SYMBOLS), "timeframe": "1Min", "start": start, "end": end,
        "limit": PAGE_LIMIT, "adjustment": config.ALPACA_ADJUSTMENT,
        "feed": config.ALPACA_FEED, "sort": "asc",
    }
    bars: dict[str, list[dict]] = {s: [] for s in SYMBOLS}
    while True:
        page = _get(session, params, day)
        for symbol, rows in (page.get("bars") or {}).items():
            bars.setdefault(symbol, []).extend(rows)
        token = page.get("next_page_token")
        if not token:
            return bars
        params["page_token"] = token


def cache_path(day: date, raw_dir: Path = config.RAW_PRICES_DIR) -> Path:
    return raw_dir / f"{day.isoformat()}.json"


def download_days(days: list[date], keys: dict[str, str], refresh: bool = False,
                  raw_dir: Path = config.RAW_PRICES_DIR) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(keys)
    fetched = 0
    for day in days:
        path = cache_path(day, raw_dir)
        if path.exists() and not refresh:
            continue
        payload = {
            "day": day.isoformat(), "feed": config.ALPACA_FEED,
            "adjustment": config.ALPACA_ADJUSTMENT,
            "downloaded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "bars": fetch_day(session, day),
        }
        tmp = path.with_suffix(".part")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
        fetched += 1
        time.sleep(0.35)                     # stays under 200 requests a minute
        if fetched % 25 == 0:
            print(f"  downloaded {fetched} days (up to {day})")
    print(f"  {fetched} days downloaded, {len(days) - fetched} read from cache")


# ------------------------------------------------------------------ parsing

def parse_day(payload: dict) -> pd.DataFrame:
    """One cached day -> long table, one row per (minute, symbol)."""
    frames = []
    for symbol, rows in payload["bars"].items():
        if rows:
            frames.append(pd.DataFrame(rows).rename(columns=_RENAME).assign(symbol=symbol))
    if not frames:
        return pd.DataFrame(columns=COLUMNS[:-1])
    df = pd.concat(frames, ignore_index=True)
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    return df[COLUMNS[:-1]]


def build_table(paths: list[Path]) -> pd.DataFrame:
    frames = [parse_day(json.loads(p.read_text(encoding="utf-8"))) for p in paths]
    frames = [f for f in frames if len(f)]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    prices = pd.concat(frames, ignore_index=True)
    prices = (prices.drop_duplicates(["ts_utc", "symbol"])
                    .sort_values(["ts_utc", "symbol"], ignore_index=True))
    # market_session is slow-ish per row, but there are only ~1,000 distinct minutes a day.
    sessions = {ts: market_session(ts) for ts in prices["ts_utc"].unique()}
    prices["session"] = prices["ts_utc"].map(sessions)
    return prices[COLUMNS]


# ------------------------------------------------------------------ checks

def regular_coverage(prices: pd.DataFrame) -> pd.DataFrame:
    """Per (New York day, symbol): regular-session bars found vs minutes expected."""
    reg = prices[prices["session"] == "regular"]
    ny_day = reg["ts_utc"].dt.tz_convert(NEW_YORK).dt.date
    found = reg.groupby([ny_day.rename("day"), "symbol"]).size().rename("found").reset_index()
    found["expected"] = [EARLY_CLOSE_MINUTES if d in config.NYSE_EARLY_CLOSES else REGULAR_MINUTES
                         for d in found["day"]]
    found["missing"] = found["expected"] - found["found"]
    return found


def summarise(prices: pd.DataFrame, days_by_episode: dict[str, list[date]]) -> None:
    print(f"\n{len(prices)} bars saved to {config.PRICES_CSV.relative_to(config.ROOT)}")
    if prices.empty:
        return
    ny_days = set(prices["ts_utc"].dt.tz_convert(NEW_YORK).dt.date)
    cov = regular_coverage(prices)
    for name, days in days_by_episode.items():
        expected = expected_trading_days(days)
        missing_days = [d for d in expected if d not in ny_days]
        holiday_data = [d for d in weekdays(days) if d in config.NYSE_HOLIDAYS and d in ny_days]
        print(f"\n[{name}] {len(expected)} trading days expected")
        print(f"  trading days with no bars: {missing_days or 'none'}")
        print(f"  NYSE holidays that DO have bars: {holiday_data or 'none'}")
        c = cov[cov["day"].isin(expected)]
        for sym, g in c.groupby("symbol"):
            print(f"  {sym}: regular-session minutes missing per day: median {g['missing'].median():.0f}, "
                  f"max {g['missing'].max()} on {g.loc[g['missing'].idxmax(), 'day']}; "
                  f"total {g['missing'].sum()} of {g['expected'].sum()}")

    # Label check: volume jumps when the market opens at 09:30:00. If `t` is the bar's
    # START, the jump is in the 09:30 bar; if it were the bar's END, it would be in 09:31.
    # (The closing auction is less useful: on many days the 15:59 rush out-trades it.)
    spy = prices[prices["symbol"] == config.MARKET]
    hm = spy["ts_utc"].dt.tz_convert(NEW_YORK).dt.strftime("%H:%M")
    med = spy.groupby(hm)["volume"].median()
    around = {m: int(med.get(m, 0)) for m in ("09:29", "09:30", "09:31")}
    verdict = "labelled by START" if around["09:30"] > 5 * around["09:29"] else "CHECK LABELS"
    print(f"\n{config.MARKET} median volume by minute: {around}  -> bars {verdict}")


# ------------------------------------------------------------------ main

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--episode", choices=sorted(config.EPISODES), action="append",
                    help="only this episode (repeatable); default all")
    ap.add_argument("--refresh", action="store_true", help="re-download cached days")
    args = ap.parse_args(argv)

    keys = load_keys()
    today = datetime.now(timezone.utc).date()
    days_by_episode = {}
    for name in args.episode or list(config.EPISODES):
        days = collection_days(config.EPISODES[name], config.PAD_DAYS, today)
        print(f"[{name}] {days[0]} to {days[-1]}")
        download_days(weekdays(days), keys, refresh=args.refresh)
        days_by_episode[name] = days

    all_days = sorted({d for ep in config.EPISODES.values()
                       for d in weekdays(collection_days(ep, config.PAD_DAYS, today))})
    paths = [cache_path(d) for d in all_days if cache_path(d).exists()]
    prices = build_table(paths)
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    prices.to_csv(config.PRICES_CSV, index=False)
    summarise(prices, days_by_episode)


if __name__ == "__main__":
    main()
