from __future__ import annotations

from datetime import datetime, timedelta, timezone

from quant_mm.forecast_store import persist_forecasts, settle_due_forecasts
from quant_mm.model_performance import model_performance_table, realized_model_weights


def _forecast_row(as_of: datetime) -> dict:
    return {
        "exchange": "gate",
        "symbol": "BTC/USDT",
        "timeframe": "15m",
        "forecast_run_at": (as_of + timedelta(minutes=16)).isoformat(),
        "as_of": as_of.isoformat(),
        "current_price": 100.0,
        "expected_return_pct": 0.4,
        "expected_price": 100.4,
        "lower_price": 99.5,
        "upper_price": 101.5,
        "up_probability": 0.60,
        "confidence": 0.60,
        "trend": "UP",
        "research_signal": "LONG_BIAS",
        "validation_direction_accuracy": 0.55,
        "candle_basis": "exchange_native_closed",
        "model_predictions_pct": {
            "good_model": 0.95,
            "bad_model": -0.70,
        },
        "model_weights": {
            "good_model": 0.50,
            "bad_model": 0.50,
        },
    }


def test_realized_model_performance_prefers_better_out_of_sample_model(tmp_path):
    results_dir = tmp_path / "forecast"
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    rows = [_forecast_row(start + timedelta(minutes=15 * i)) for i in range(40)]
    assert persist_forecasts(rows, results_dir) == 40

    def fake_fetcher(exchange, symbol, timeframe, candle_open_at):
        return {
            "timestamp": candle_open_at,
            "open": 100.0,
            "high": 101.5,
            "low": 99.5,
            "close": 101.0,
            "volume": 1000.0,
        }

    result = settle_due_forecasts(
        results_dir,
        candle_fetcher=fake_fetcher,
        now=datetime(2026, 2, 1, tzinfo=timezone.utc),
        limit=100,
    )
    assert result["settled"] == 40

    table = model_performance_table(
        results_dir,
        symbol="BTC/USDT",
        timeframe="15m",
        candle_basis="exchange_native_closed",
        limit=100,
    )
    assert set(table["model"]) == {"good_model", "bad_model"}

    good = table[table["model"] == "good_model"].iloc[0]
    bad = table[table["model"] == "bad_model"].iloc[0]
    assert good["direction_accuracy"] == 1.0
    assert bad["direction_accuracy"] == 0.0
    assert good["mae_pct"] < bad["mae_pct"]
    assert good["realized_weight"] > bad["realized_weight"]

    weights = realized_model_weights(
        results_dir,
        symbol="BTC/USDT",
        timeframe="15m",
        candle_basis="exchange_native_closed",
        min_samples=30,
        limit=100,
    )
    assert weights["good_model"] > weights["bad_model"]
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_realized_weights_wait_for_minimum_sample_size(tmp_path):
    results_dir = tmp_path / "forecast"
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    rows = [_forecast_row(start + timedelta(minutes=15 * i)) for i in range(10)]
    persist_forecasts(rows, results_dir)

    settle_due_forecasts(
        results_dir,
        candle_fetcher=lambda *args: {"close": 101.0},
        now=datetime(2026, 2, 1, tzinfo=timezone.utc),
        limit=100,
    )

    weights = realized_model_weights(
        results_dir,
        symbol="BTC/USDT",
        timeframe="15m",
        candle_basis="exchange_native_closed",
        min_samples=30,
    )
    assert weights == {}
