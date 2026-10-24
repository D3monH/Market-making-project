import numpy as np
import pandas as pd

from quant_mm.features import FEATURE_COLUMNS, build_features
from quant_mm.market_maker import MarketMakerConfig, make_quote
from quant_mm.model import LinearModelConfig, walk_forward_predictions


def synthetic_ohlcv(rows: int = 900) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=rows, freq="15min", tz="UTC")
    x = np.arange(rows)
    close = 100.0 + 0.02 * x + np.sin(x / 17.0)
    return pd.DataFrame(
        {
            "open": close - 0.05,
            "high": close + 0.30,
            "low": close - 0.30,
            "close": close,
            "volume": 1000.0 + 50.0 * np.cos(x / 11.0),
        },
        index=idx,
    )


def test_walk_forward_predictions_are_generated():
    frame = build_features(synthetic_ohlcv(), horizon_bars=4)
    cfg = LinearModelConfig(
        horizon_bars=4,
        train_window=500,
        min_train_rows=200,
        retrain_every=20,
    )
    pred = walk_forward_predictions(frame, FEATURE_COLUMNS, cfg)
    assert pred.notna().sum() > 100
    assert pred.iloc[:150].isna().all()


def test_long_inventory_moves_reservation_price_down():
    cfg = MarketMakerConfig(max_inventory_usdt=1000.0, inventory_skew_bps=20.0)
    flat = make_quote(100.0, 0.0, 0.001, 0.0, cfg)
    long = make_quote(100.0, 0.0, 0.001, 5.0, cfg)
    assert long.reservation_price < flat.reservation_price
    assert long.bid < long.ask


def test_positive_model_signal_moves_reservation_price_up():
    cfg = MarketMakerConfig(signal_weight=1.0, max_signal_bps=50.0)
    neutral = make_quote(100.0, 0.0, 0.001, 0.0, cfg)
    bullish = make_quote(100.0, 0.002, 0.001, 0.0, cfg)
    assert bullish.reservation_price > neutral.reservation_price
