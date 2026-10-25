from __future__ import annotations

import pandas as pd

from quant_mm.event_contract_store import (
    event_accuracy_summary,
    load_event_records,
    persist_event_prediction,
    settle_due_index_events,
)


def _row() -> dict:
    return {
        "asset": "BTC",
        "symbol": "BTC/USDT",
        "timeframe": "15m",
        "benchmark": "INDEX",
        "data_mode": "official_gate_index",
        "forecast_run_at": "2026-10-25T12:16:00+00:00",
        "event_open_at": "2026-10-25T12:15:00+00:00",
        "event_close_at": "2026-10-25T12:30:00+00:00",
        "model_base_price": 80000.0,
        "expected_price": 80100.0,
        "expected_return_pct": 0.125,
        "raw_up_probability": 0.62,
        "final_up_probability": 0.68,
        "final_down_probability": 0.32,
        "predicted_side": "UP",
        "confidence": 0.65,
        "alpha_score": 0.3,
        "alpha_confidence": 0.7,
        "alpha_data_quality": 0.9,
        "up_market_price": 0.55,
        "down_market_price": 0.47,
        "selected_side": "UP",
        "expected_edge": 0.13,
        "expected_roi_pct": 23.6,
        "status": "PENDING",
    }


def test_same_gate_event_is_persisted_only_once(tmp_path):
    eid1, inserted1 = persist_event_prediction(_row(), tmp_path)
    changed = _row()
    changed["final_up_probability"] = 0.90
    eid2, inserted2 = persist_event_prediction(changed, tmp_path)

    assert eid1 == eid2
    assert inserted1 is True
    assert inserted2 is False
    records = load_event_records(tmp_path)
    assert len(records) == 1
    assert float(records.iloc[0]["final_up_probability"]) == 0.68


def test_index_settlement_uses_event_candle_open_and_close(tmp_path):
    persist_event_prediction(_row(), tmp_path)

    def candle_fetcher(asset, timeframe, event_open_at):
        assert asset == "BTC"
        assert timeframe == "15m"
        return {"open": 80020.0, "close": 80150.0, "high": 80200.0, "low": 79900.0}

    result = settle_due_index_events(
        tmp_path,
        candle_fetcher,
        now=pd.Timestamp("2026-10-25T12:31:00Z"),
    )
    assert result["settled"] == 1

    records = load_event_records(tmp_path)
    settled = records.iloc[0]
    assert settled["status"] == "SETTLED"
    assert settled["actual_side"] == "UP"
    assert int(settled["direction_correct"]) == 1
    assert float(settled["actual_reference_price"]) == 80020.0
    assert float(settled["actual_settlement_price"]) == 80150.0
    assert round(float(settled["realized_pnl_per_share"]), 6) == 0.45

    summary = event_accuracy_summary(tmp_path, asset="BTC")
    assert len(summary) == 1
    assert float(summary.iloc[0]["direction_accuracy"]) == 1.0
