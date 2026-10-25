from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from quant_mm.auto_forecast import (
    load_auto_settings,
    normalize_auto_settings,
    run_auto_forecast_cycle,
    save_auto_settings,
)


def test_auto_settings_round_trip(tmp_path):
    saved = save_auto_settings(
        tmp_path,
        {
            "enabled": True,
            "exchange": "gateio",
            "symbols": ["btc/usdt", "BTC/USDT"],
            "timeframes": ["15M", "15m"],
            "bars": 12000,
            "poll_seconds": 5,
        },
    )
    assert saved.exists()
    loaded = load_auto_settings(tmp_path)
    assert loaded["enabled"] is True
    assert loaded["exchange"] == "gate"
    assert loaded["symbols"] == ["BTC/USDT"]
    assert loaded["timeframes"] == ["15m"]
    assert loaded["bars"] == 10000
    assert loaded["poll_seconds"] == 30


def test_auto_cycle_skips_existing_target_without_retraining(tmp_path):
    market = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
        index=pd.DatetimeIndex([pd.Timestamp("2026-10-25T12:00:00Z")], name="timestamp"),
    )
    forecast_calls = []
    saved_rows = []

    def market_loader(*args, **kwargs):
        return market

    def existing_loader(*args, **kwargs):
        return {"forecast_id": "already-there"}

    def forecast_func(*args, **kwargs):
        forecast_calls.append(1)
        raise AssertionError("existing target must not retrain")

    def saver(rows, results_dir):
        saved_rows.extend(rows)

    result = run_auto_forecast_cycle(
        {
            "enabled": True,
            "exchange": "gate",
            "symbols": ["BTC/USDT"],
            "timeframes": ["15m"],
            "bars": 5000,
            "poll_seconds": 60,
        },
        {"forecast": {}, "time": {"daily_candle_timezone": "UTC"}},
        results_dir=tmp_path,
        market_loader=market_loader,
        existing_loader=existing_loader,
        forecast_func=forecast_func,
        weight_loader=lambda *a, **k: {},
        saver=saver,
        settle=False,
    )

    assert result["generated"] == 0
    assert result["skipped_existing"] == 1
    assert forecast_calls == []
    assert saved_rows == []


@dataclass
class FakeForecast:
    def to_dict(self):
        return {
            "timeframe": "15m",
            "as_of": "2026-10-25T12:00:00+00:00",
            "current_price": 80000.0,
            "expected_return_pct": 0.2,
            "expected_price": 80160.0,
            "lower_price": 79800.0,
            "upper_price": 80400.0,
            "up_probability": 0.61,
            "confidence": 0.55,
            "trend": "UP",
            "research_signal": "LONG_BIAS",
            "validation_direction_accuracy": 0.54,
            "model_predictions_pct": {},
            "model_weights": {},
        }


def test_auto_cycle_generates_new_target_once(tmp_path):
    market = pd.DataFrame(
        {"open": [80000.0], "high": [80100.0], "low": [79900.0], "close": [80000.0], "volume": [1.0]},
        index=pd.DatetimeIndex([pd.Timestamp("2026-10-25T12:00:00Z")], name="timestamp"),
    )
    saved_rows = []

    result = run_auto_forecast_cycle(
        normalize_auto_settings(
            {
                "enabled": True,
                "exchange": "gate",
                "symbols": ["BTC/USDT"],
                "timeframes": ["15m"],
            }
        ),
        {"forecast": {}, "time": {"daily_candle_timezone": "UTC"}},
        results_dir=tmp_path,
        market_loader=lambda *a, **k: market,
        existing_loader=lambda *a, **k: None,
        forecast_func=lambda *a, **k: FakeForecast(),
        weight_loader=lambda *a, **k: {},
        saver=lambda rows, results_dir: saved_rows.extend(rows),
        settle=False,
    )

    assert result["generated"] == 1
    assert result["skipped_existing"] == 0
    assert len(saved_rows) == 1
    assert saved_rows[0]["forecast_source"] == "auto"
    assert saved_rows[0]["symbol"] == "BTC/USDT"
