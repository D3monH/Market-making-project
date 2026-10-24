from __future__ import annotations

import pandas as pd

import quant_mm.market_cache as market_cache


def _frame(count: int, timeframe: str = "15min") -> pd.DataFrame:
    delta = pd.Timedelta(timeframe)
    current_open = pd.Timestamp.now(tz="UTC").floor(timeframe)
    last_closed_open = current_open - delta
    index = pd.date_range(end=last_closed_open, periods=count, freq=timeframe)
    rows = []
    for i, _ in enumerate(index):
        price = 100.0 + i
        rows.append(
            {
                "open": price,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price + 0.5,
                "volume": 10.0 + i,
            }
        )
    return pd.DataFrame(rows, index=index)


def test_cache_cold_start_then_small_incremental_refresh(tmp_path, monkeypatch):
    calls: list[int] = []
    full = _frame(100)

    def fake_fetch(exchange, symbol, timeframe, bars, daily_timezone="UTC"):
        calls.append(int(bars))
        return full.tail(bars).copy()

    monkeypatch.setattr(market_cache, "fetch_forecast_ohlcv", fake_fetch)

    first = market_cache.load_cached_forecast_ohlcv(
        "gate",
        "BTC/USDT",
        "15m",
        80,
        cache_dir=tmp_path,
        use_cache=True,
    )
    assert calls == [80]
    assert first.attrs["cache_refresh_mode"] == "cold_start"
    assert first.attrs["cache_rows_total"] == 80
    assert first.attrs["downloaded_rows_this_run"] == 80

    second = market_cache.load_cached_forecast_ohlcv(
        "gate",
        "BTC/USDT",
        "15m",
        80,
        cache_dir=tmp_path,
        use_cache=True,
    )
    assert len(calls) == 2
    assert calls[1] < 80
    assert calls[1] >= 12
    assert second.attrs["cache_refresh_mode"] == "incremental"
    assert second.attrs["cache_rows_total"] == 80
    assert second.attrs["missing_intervals"] == 0


def test_recent_cache_can_survive_temporary_network_failure(tmp_path, monkeypatch):
    full = _frame(60)
    calls = {"count": 0}

    def fake_fetch(exchange, symbol, timeframe, bars, daily_timezone="UTC"):
        calls["count"] += 1
        if calls["count"] == 1:
            return full.tail(bars).copy()
        raise RuntimeError("simulated network timeout")

    monkeypatch.setattr(market_cache, "fetch_forecast_ohlcv", fake_fetch)

    market_cache.load_cached_forecast_ohlcv(
        "gate", "BTC/USDT", "15m", 50, cache_dir=tmp_path, use_cache=True
    )
    cached = market_cache.load_cached_forecast_ohlcv(
        "gate", "BTC/USDT", "15m", 50, cache_dir=tmp_path, use_cache=True
    )

    assert cached.attrs["cache_refresh_mode"] == "offline_cache"
    assert "simulated network timeout" in cached.attrs["cache_refresh_error"]
    assert len(cached) == 50
