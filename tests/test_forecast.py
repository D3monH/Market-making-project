import numpy as np
import pandas as pd

from quant_mm.forecast_engine import (
    FORECAST_FEATURES,
    ForecastConfig,
    build_forecast_features,
    forecast_next_candle,
)


def synthetic_market(rows: int = 1400, drift: float = 0.0008) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=rows, freq="15min", tz="UTC")
    x = np.arange(rows, dtype=float)
    returns = drift + 0.0007 * np.sin(x / 11.0) + 0.0003 * np.cos(x / 29.0)
    close = 100.0 * np.cumprod(1.0 + returns)
    open_ = close / (1.0 + returns * 0.4)
    wiggle = 0.0015 + 0.0003 * (1.0 + np.sin(x / 17.0))
    high = np.maximum(open_, close) * (1.0 + wiggle)
    low = np.minimum(open_, close) * (1.0 - wiggle)
    volume = 1000.0 + 80.0 * np.sin(x / 13.0) + 30.0 * np.cos(x / 7.0)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_forecast_features_are_causal_and_target_last_row_is_unknown():
    frame = build_forecast_features(synthetic_market())
    assert frame.iloc[-1][FORECAST_FEATURES].notna().all()
    assert pd.isna(frame.iloc[-1]["target_return"])


def test_forecast_uses_latest_closed_candle_close_as_base_price():
    market = synthetic_market()
    cfg = ForecastConfig(min_train_rows=500, train_window=1200)
    result = forecast_next_candle(market, "15m", cfg)

    assert result.current_price == market.iloc[-1]["close"]
    assert pd.Timestamp(result.as_of) == market.index[-1]


def test_forecast_returns_probabilistic_metadata():
    cfg = ForecastConfig(min_train_rows=500, train_window=1200)
    result = forecast_next_candle(synthetic_market(), "15m", cfg)

    assert result.current_price > 0
    assert result.lower_price < result.upper_price
    assert 0.0 <= result.up_probability <= 1.0
    assert 0.0 <= result.confidence <= 1.0
    assert 0.0 <= result.model_agreement <= 1.0
    assert set(result.model_predictions_pct) == {"linear", "ridge", "hist_gb"}
    assert abs(sum(result.model_weights.values()) - 1.0) < 1e-9
    assert result.training_rows >= 500


def test_positive_drift_does_not_produce_invalid_signal():
    cfg = ForecastConfig(min_train_rows=500, train_window=1200)
    result = forecast_next_candle(synthetic_market(drift=0.0012), "1h", cfg)
    assert result.trend in {"STRONG_UP", "UP", "SIDEWAYS", "DOWN", "STRONG_DOWN"}
    assert result.research_signal in {"LONG_BIAS", "SHORT_BIAS", "WAIT"}
