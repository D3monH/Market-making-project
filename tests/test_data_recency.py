from __future__ import annotations

import sys
from types import SimpleNamespace

import pandas as pd

from quant_mm.data import _initial_since_ms, fetch_ohlcv


class FakeNetworkError(Exception):
    pass


def test_initial_since_keeps_small_history_buffer():
    now_ms = 10_000_000
    timeframe_ms = 60_000
    bars = 100
    since = _initial_since_ms(now_ms, timeframe_ms, bars)
    assert since == now_ms - int(bars * timeframe_ms * 1.10)


def test_fetch_ohlcv_paginates_to_latest_closed_candle(monkeypatch):
    timeframe_ms = 60_000
    now_ms = int(25.5 * timeframe_ms)

    rows = []
    for i in range(26):
        price = 100.0 + i
        rows.append([i * timeframe_ms, price, price + 1, price - 1, price + 0.5, 10.0])

    class FakeGate:
        rateLimit = 0
        has = {"fetchOHLCV": True, "fetchTicker": True}

        def __init__(self, _config):
            pass

        def load_markets(self):
            return None

        def parse_timeframe(self, timeframe):
            assert timeframe == "1m"
            return 60

        def milliseconds(self):
            return now_ms

        def fetch_ohlcv(self, symbol, timeframe, since, limit):
            assert symbol == "BTC/USDT"
            # Simulate an exchange that silently caps responses below the
            # requested page size. Returning only five rows must not make the
            # downloader stop early.
            eligible = [row for row in rows if row[0] >= since]
            return eligible[:5]

    monkeypatch.setitem(
        sys.modules,
        "ccxt",
        SimpleNamespace(gate=FakeGate, NetworkError=FakeNetworkError),
    )

    result = fetch_ohlcv("gate", "BTC/USDT", "1m", bars=20)

    assert len(result) == 20
    expected_latest = pd.Timestamp(24 * timeframe_ms, unit="ms", tz="UTC")
    assert result.index[-1] == expected_latest
    assert result.iloc[-1]["close"] == 124.5
