"""Tests for src/collect_prices.py. Offline: a fake session stands in for Alpaca."""

from datetime import date

import pandas as pd
import pytest

from src import collect_prices as cp


def bar(t, c, v=100):
    return {"t": t, "o": c, "h": c, "l": c, "c": c, "v": v, "n": 3, "vw": c}


def test_day_bounds_follow_daylight_saving():
    # EST (UTC-5) on Fri 6 Mar 2026, EDT (UTC-4) on Mon 9 Mar 2026
    assert cp.day_bounds(date(2026, 3, 6)) == ("2026-03-06T09:00:00Z", "2026-03-07T00:59:00Z")
    assert cp.day_bounds(date(2026, 3, 9)) == ("2026-03-09T08:00:00Z", "2026-03-09T23:59:00Z")


def test_trading_days_skip_weekends_and_holidays():
    days = [date(2025, 6, 18), date(2025, 6, 19), date(2025, 6, 20), date(2025, 6, 21)]
    assert cp.weekdays(days) == days[:3]
    assert cp.expected_trading_days(days) == [date(2025, 6, 18), date(2025, 6, 20)]  # Juneteenth out


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status_code, self.text = payload, status, str(payload)

    def json(self):
        return self.payload


class FakeSession:
    """Returns the given pages in order and records the params it was called with."""
    def __init__(self, pages):
        self.pages, self.calls = list(pages), []

    def get(self, url, params, timeout):
        self.calls.append(dict(params))
        return FakeResponse(self.pages.pop(0))


def test_fetch_day_follows_pagination():
    s = FakeSession([
        {"bars": {"ITA": [bar("2025-06-16T13:30:00Z", 180.0)]}, "next_page_token": "abc"},
        {"bars": {"ITA": [bar("2025-06-16T13:31:00Z", 180.5)],
                  "SPY": [bar("2025-06-16T13:30:00Z", 600.0)]}, "next_page_token": None},
    ])
    bars = cp.fetch_day(s, date(2025, 6, 16))
    assert [b["c"] for b in bars["ITA"]] == [180.0, 180.5]
    assert len(bars["SPY"]) == 1
    assert "page_token" not in s.calls[0] and s.calls[1]["page_token"] == "abc"
    assert s.calls[0]["symbols"] == "ITA,SPY" and s.calls[0]["timeframe"] == "1Min"


def test_auth_error_is_not_retried():
    class Refuses(FakeSession):
        def get(self, url, params, timeout):
            self.calls.append(params)
            return FakeResponse({"message": "forbidden"}, status=403)
    s = Refuses([])
    with pytest.raises(RuntimeError, match="refused"):
        cp.fetch_day(s, date(2025, 6, 16))
    assert len(s.calls) == 1


def test_build_table_parses_and_labels_sessions(tmp_path):
    import json
    payload = {"bars": {
        "ITA": [bar("2025-06-16T13:29:00Z", 179.0), bar("2025-06-16T13:30:00Z", 180.0)],
        "SPY": [bar("2025-06-16T13:30:00Z", 600.0), bar("2025-06-16T20:00:00Z", 601.0, v=9999)],
    }}
    p = tmp_path / "2025-06-16.json"
    p.write_text(json.dumps(payload))
    prices = cp.build_table([p, p])                       # same day twice -> deduped
    assert list(prices.columns) == cp.COLUMNS
    assert len(prices) == 4
    assert str(prices["ts_utc"].dt.tz) == "UTC"
    by = prices.set_index(["symbol", "ts_utc"])["session"]
    assert by[("ITA", pd.Timestamp("2025-06-16 13:29", tz="UTC"))] == "pre"
    assert by[("ITA", pd.Timestamp("2025-06-16 13:30", tz="UTC"))] == "regular"
    assert by[("SPY", pd.Timestamp("2025-06-16 20:00", tz="UTC"))] == "post"


def test_regular_coverage_counts_missing_minutes():
    ts = pd.date_range("2025-06-16 13:30", periods=388, freq="min", tz="UTC")   # 2 short
    prices = pd.DataFrame({"ts_utc": ts, "symbol": "ITA", "session": "regular"})
    cov = cp.regular_coverage(prices)
    assert cov.loc[0, ["found", "expected", "missing"]].tolist() == [388, 390, 2]


def test_missing_keys_message(tmp_path, monkeypatch):
    monkeypatch.setattr(cp.config, "ROOT", tmp_path)
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    (tmp_path / ".env").write_text("ALPACA_API_KEY_ID=paste_your_key_id_here\n")
    with pytest.raises(RuntimeError, match="ALPACA_API_KEY_ID, ALPACA_API_SECRET_KEY"):
        cp.load_keys()
