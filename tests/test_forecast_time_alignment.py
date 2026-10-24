from __future__ import annotations

import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from quant_mm.dashboard_utils import forecast_window, format_display_timestamp
from quant_mm.data import (
    ensure_recent_closed_data,
    fetch_closed_candle,
    resample_daily_ohlcv,
)


def _four_hour_market(days: int = 3) -> pd.DataFrame:
    periods = days * 6
    idx = pd.date_range(
        "2026-10-21T16:00:00Z",
        periods=periods,
        freq="4h",
    )
    rows = []
    for i in range(periods):
        open_price = 100.0 + i
        rows.append(
            {
                "open": open_price,
                "high": open_price + 1.0,
                "low": open_price - 1.0,
                "close": open_price + 0.5,
                "volume": 10.0 + i,
            }
        )
    return pd.DataFrame(rows, index=idx)


def test_beijing_daily_resample_uses_natural_midnight_boundaries():
    base = _four_hour_market(days=3)
    daily = resample_daily_ohlcv(
        base,
        timezone_name="Asia/Shanghai",
        base_timeframe="4h",
        now="2026-10-24T16:01:00Z",
    )

    assert len(daily) == 3
    # Beijing 00:00 is previous-day 16:00 UTC.
    assert daily.index[0] == pd.Timestamp("2026-10-21T16:00:00Z")
    assert daily.index[1] == pd.Timestamp("2026-10-22T16:00:00Z")
    assert daily.index[2] == pd.Timestamp("2026-10-23T16:00:00Z")

    first = daily.iloc[0]
    assert first["open"] == pytest.approx(100.0)
    assert first["high"] == pytest.approx(106.0)
    assert first["low"] == pytest.approx(99.0)
    assert first["close"] == pytest.approx(105.5)
    assert first["volume"] == pytest.approx(sum(10.0 + i for i in range(6)))


def test_beijing_daily_resample_drops_current_incomplete_day():
    base = _four_hour_market(days=2).iloc[:7]
    daily = resample_daily_ohlcv(
        base,
        timezone_name="Asia/Shanghai",
        base_timeframe="4h",
        now="2026-10-22T20:00:00Z",
    )

    assert len(daily) == 1
    assert daily.index[0] == pd.Timestamp("2026-10-21T16:00:00Z")


def test_15m_forecast_window_matches_beijing_2015_to_2030_example():
    # 12:00 UTC == 20:00 Beijing. This candle closes at 20:15 and the
    # forecast target closes at 20:30.
    window = forecast_window("2026-10-24T12:00:00Z", "15m")
    assert format_display_timestamp(
        window["base_close_at"], "Asia/Shanghai"
    ) == "2026-10-24 20:15"
    assert format_display_timestamp(
        window["target_close_at"], "Asia/Shanghai"
    ) == "2026-10-24 20:30"


def test_beijing_daily_forecast_window_closes_at_local_midnight():
    # Beijing day 2026-10-23 opens at 2026-10-22 16:00 UTC.
    window = forecast_window("2026-10-22T16:00:00Z", "1d")
    assert format_display_timestamp(
        window["base_close_at"], "Asia/Shanghai"
    ) == "2026-10-24 00:00"
    assert format_display_timestamp(
        window["target_close_at"], "Asia/Shanghai"
    ) == "2026-10-25 00:00"


def test_stale_forecast_input_is_rejected():
    frame = pd.DataFrame(
        {"close": [100.0]},
        index=pd.DatetimeIndex([pd.Timestamp("2026-10-24T12:00:00Z")]),
    )

    # Latest 15m candle closes at 12:15 UTC. At 12:29 it is still the correct
    # most recent completed candle because 12:15-12:30 is still forming.
    ensure_recent_closed_data(frame, "15m", now="2026-10-24T12:29:00Z")

    with pytest.raises(RuntimeError, match="stale"):
        ensure_recent_closed_data(frame, "15m", now="2026-10-24T12:40:00Z")


def test_daily_verification_aggregates_exact_stored_24h_window(monkeypatch):
    target = pd.Timestamp("2026-10-22T16:00:00Z")
    target_ms = int(target.timestamp() * 1000)
    four_hours_ms = 4 * 60 * 60 * 1000
    rows = []
    for i in range(6):
        price = 200.0 + i
        rows.append(
            [
                target_ms + i * four_hours_ms,
                price,
                price + 2.0,
                price - 2.0,
                price + 0.5,
                100.0 + i,
            ]
        )

    class FakeGate:
        rateLimit = 0
        has = {"fetchOHLCV": True, "fetchTicker": True}

        def __init__(self, _config):
            pass

        def load_markets(self):
            return None

        def parse_timeframe(self, timeframe):
            assert timeframe == "4h"
            return 4 * 60 * 60

        def milliseconds(self):
            return target_ms + 25 * 60 * 60 * 1000

        def fetch_ohlcv(self, symbol, timeframe, since, limit):
            assert symbol == "BTC/USDT"
            assert timeframe == "4h"
            assert since == target_ms
            return rows

    monkeypatch.setitem(sys.modules, "ccxt", SimpleNamespace(gate=FakeGate))

    candle = fetch_closed_candle(
        "gate",
        "BTC/USDT",
        "1d",
        target.isoformat(),
    )

    assert candle is not None
    assert candle["timestamp"] == target.isoformat()
    assert candle["open"] == pytest.approx(200.0)
    assert candle["high"] == pytest.approx(207.0)
    assert candle["low"] == pytest.approx(198.0)
    assert candle["close"] == pytest.approx(205.5)
    assert candle["volume"] == pytest.approx(sum(100.0 + i for i in range(6)))
