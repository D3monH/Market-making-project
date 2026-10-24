from __future__ import annotations

from datetime import datetime, timezone

import pytest

from quant_mm.forecast_store import (
    accuracy_summary,
    load_forecast_records,
    pending_due_forecasts,
    persist_forecasts,
    settle_due_forecasts,
    store_status,
)


def sample_forecast(**overrides):
    row = {
        "exchange": "gate",
        "symbol": "BTC/USDT",
        "timeframe": "15m",
        "forecast_run_at": "2026-10-24T10:16:00+00:00",
        "as_of": "2026-10-24T10:00:00+00:00",
        "current_price": 100.0,
        "expected_return_pct": 1.0,
        "expected_price": 101.0,
        "lower_price": 99.0,
        "upper_price": 102.0,
        "up_probability": 0.67,
        "confidence": 0.71,
        "trend": "UP",
        "research_signal": "LONG_BIAS",
        "validation_direction_accuracy": 0.55,
        "model_predictions_pct": {"linear": 0.8, "ridge": 1.0, "hist_gb": 1.2},
        "model_weights": {"linear": 0.3, "ridge": 0.4, "hist_gb": 0.3},
    }
    row.update(overrides)
    return row


def test_forecast_target_times_and_pending_boundary(tmp_path):
    results_dir = tmp_path / "forecast"
    assert persist_forecasts([sample_forecast()], results_dir) == 1

    records = load_forecast_records(results_dir)
    assert len(records) == 1
    row = records.iloc[0]
    assert row["target_open_at"].startswith("2026-10-24T10:15:00")
    assert row["target_close_at"].startswith("2026-10-24T10:30:00")
    assert row["status"] == "PENDING"

    before_close = datetime(2026, 10, 24, 10, 29, 59, tzinfo=timezone.utc)
    at_close = datetime(2026, 10, 24, 10, 30, 0, tzinfo=timezone.utc)
    assert pending_due_forecasts(results_dir, now=before_close) == []
    assert len(pending_due_forecasts(results_dir, now=at_close)) == 1


def test_repeated_click_same_base_candle_does_not_create_duplicate(tmp_path):
    results_dir = tmp_path / "forecast"
    first = sample_forecast(
        forecast_run_at="2026-10-24T10:16:00+00:00",
        expected_price=101.0,
    )
    repeated = sample_forecast(
        forecast_run_at="2026-10-24T10:29:00+00:00",
        expected_price=105.0,
        expected_return_pct=5.0,
    )

    assert persist_forecasts([first], results_dir) == 1
    assert persist_forecasts([repeated], results_dir) == 0

    records = load_forecast_records(results_dir)
    assert len(records) == 1
    row = records.iloc[0]
    assert row["forecast_run_at"] == "2026-10-24T10:16:00+00:00"
    assert row["expected_price"] == pytest.approx(101.0)
    assert row["target_close_at"].startswith("2026-10-24T10:30:00")


def test_new_closed_base_candle_creates_new_forecast(tmp_path):
    results_dir = tmp_path / "forecast"
    first = sample_forecast(as_of="2026-10-24T10:00:00+00:00")
    next_candle = sample_forecast(
        forecast_run_at="2026-10-24T10:31:00+00:00",
        as_of="2026-10-24T10:15:00+00:00",
        current_price=101.5,
        expected_price=102.0,
    )

    assert persist_forecasts([first], results_dir) == 1
    assert persist_forecasts([next_candle], results_dir) == 1
    records = load_forecast_records(results_dir)
    assert len(records) == 2


def test_settlement_records_actual_result_and_accuracy(tmp_path):
    results_dir = tmp_path / "forecast"
    persist_forecasts([sample_forecast()], results_dir)

    calls = []

    def fake_fetcher(exchange, symbol, timeframe, candle_open_at):
        calls.append((exchange, symbol, timeframe, candle_open_at))
        return {
            "timestamp": candle_open_at,
            "open": 100.2,
            "high": 102.0,
            "low": 99.8,
            "close": 101.5,
            "volume": 1234.0,
        }

    result = settle_due_forecasts(
        results_dir,
        candle_fetcher=fake_fetcher,
        now=datetime(2026, 10, 24, 10, 31, tzinfo=timezone.utc),
    )
    assert result["settled"] == 1
    assert len(calls) == 1
    assert calls[0][3].startswith("2026-10-24T10:15:00")

    records = load_forecast_records(results_dir)
    row = records.iloc[0]
    assert row["status"] == "SETTLED"
    assert row["actual_close"] == pytest.approx(101.5)
    assert row["actual_return_pct"] == pytest.approx(1.5)
    assert int(row["direction_correct"]) == 1
    assert int(row["signal_correct"]) == 1
    assert int(row["interval_hit"]) == 1
    assert row["return_abs_error_pct"] == pytest.approx(0.5)
    assert row["price_abs_error_pct"] == pytest.approx(abs(101.0 - 101.5) / 101.5 * 100.0)

    summary = accuracy_summary(results_dir, symbol="BTC/USDT")
    assert len(summary) == 1
    stats = summary.iloc[0]
    assert int(stats["settled_count"]) == 1
    assert stats["direction_accuracy"] == pytest.approx(1.0)
    assert stats["signal_accuracy"] == pytest.approx(1.0)
    assert stats["interval_hit_rate"] == pytest.approx(1.0)

    status = store_status(results_dir)
    assert status["total"] == 1
    assert status["pending"] == 0
    assert status["settled"] == 1


def test_settlement_waits_when_exchange_has_no_closed_candle(tmp_path):
    results_dir = tmp_path / "forecast"
    persist_forecasts([sample_forecast()], results_dir)

    result = settle_due_forecasts(
        results_dir,
        candle_fetcher=lambda *args: None,
        now=datetime(2026, 10, 24, 10, 31, tzinfo=timezone.utc),
    )
    assert result["settled"] == 0
    records = load_forecast_records(results_dir)
    assert records.iloc[0]["status"] == "PENDING"
