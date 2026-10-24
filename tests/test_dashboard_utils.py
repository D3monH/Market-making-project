from __future__ import annotations

import json

import pandas as pd

from quant_mm.dashboard_utils import (
    build_price_projection,
    load_forecast_history,
    save_forecast_run,
    timeframe_to_timedelta,
)


def test_timeframe_to_timedelta():
    assert timeframe_to_timedelta("15m") == pd.Timedelta(minutes=15)
    assert timeframe_to_timedelta("4h") == pd.Timedelta(hours=4)
    assert timeframe_to_timedelta("1d") == pd.Timedelta(days=1)


def test_build_price_projection_adds_one_future_point():
    idx = pd.date_range("2026-01-01", periods=5, freq="15min", tz="UTC")
    market = pd.DataFrame({"close": [100, 101, 102, 103, 104]}, index=idx)
    chart = build_price_projection(market, expected_price=105.5, timeframe="15m", history_points=5)

    assert chart.index[-1] == idx[-1] + pd.Timedelta(minutes=15)
    assert chart.loc[idx[-1], "预测收盘价"] == 104.0
    assert chart.iloc[-1]["预测收盘价"] == 105.5


def test_save_and_load_forecast_history(tmp_path):
    rows = [
        {
            "symbol": "BTC/USDT",
            "timeframe": "15m",
            "trend": "UP",
            "expected_return_pct": 0.1,
        }
    ]
    latest_path, history_path = save_forecast_run(rows, tmp_path)

    assert latest_path.exists()
    assert history_path.exists()
    assert json.loads(latest_path.read_text(encoding="utf-8"))[0]["symbol"] == "BTC/USDT"

    history = load_forecast_history(tmp_path)
    assert len(history) == 1
    assert history.iloc[0]["timeframe"] == "15m"
