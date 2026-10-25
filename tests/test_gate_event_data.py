from __future__ import annotations

import pandas as pd

import quant_mm.gate_event_data as ged


def _frame(start: str, periods: int, timeframe: str, volume: float) -> pd.DataFrame:
    freq = {"5m": "5min", "15m": "15min"}[timeframe]
    idx = pd.date_range(start, periods=periods, freq=freq, tz="UTC")
    values = list(range(periods))
    return pd.DataFrame(
        {
            "open": [100.0 + v for v in values],
            "high": [101.0 + v for v in values],
            "low": [99.0 + v for v in values],
            "close": [100.5 + v for v in values],
            "volume": [volume for _ in values],
        },
        index=idx,
    )


def test_index_market_scope_matches_current_gate_event_rollout():
    assert ged.supports_index_market("BTC", "5m")
    assert ged.supports_index_market("ETH", "15m")
    assert not ged.supports_index_market("SOL", "5m")
    assert not ged.supports_index_market("BTC", "1h")


def test_index_loader_keeps_index_prices_and_companion_futures_volume(monkeypatch, tmp_path):
    index = _frame("2026-10-20T00:00:00Z", 1000, "15m", 0.0)
    futures = index.copy()
    futures["open"] = futures["open"] + 5000.0
    futures["high"] = futures["high"] + 5000.0
    futures["low"] = futures["low"] + 5000.0
    futures["close"] = futures["close"] + 5000.0
    futures["volume"] = 123.0

    def fake_fetch(asset, timeframe, *, limit=1900, index_price=False):
        return index.copy() if index_price else futures.copy()

    monkeypatch.setattr(ged, "fetch_gate_futures_candles", fake_fetch)
    result = ged.load_gate_index_market("BTC", "15m", bars=900, cache_dir=tmp_path)

    assert len(result) == 900
    assert result.iloc[-1]["close"] == index.iloc[-1]["close"]
    assert result.iloc[-1]["volume"] == 123.0
    assert result.attrs["price_source"] == "gate_futures_index"
