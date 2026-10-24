from __future__ import annotations

import numpy as np
import pandas as pd

FEATURE_COLUMNS = [
    "ret_1",
    "ret_3",
    "ret_6",
    "ret_12",
    "volatility_12",
    "volume_z_48",
    "range_pct",
    "ema_distance",
    "rsi_14",
]


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def build_features(df: pd.DataFrame, horizon_bars: int = 4) -> pd.DataFrame:
    """Build causal features plus a future-return training target."""
    out = df.copy().sort_index()
    close = out["close"].astype(float)
    volume = out["volume"].astype(float)

    out["ret_1"] = close.pct_change(1)
    out["ret_3"] = close.pct_change(3)
    out["ret_6"] = close.pct_change(6)
    out["ret_12"] = close.pct_change(12)
    out["volatility_12"] = out["ret_1"].rolling(12).std()

    volume_mean = volume.rolling(48).mean()
    volume_std = volume.rolling(48).std().replace(0.0, np.nan)
    out["volume_z_48"] = (volume - volume_mean) / volume_std

    out["range_pct"] = (out["high"] - out["low"]) / close.replace(0.0, np.nan)
    ema_fast = close.ewm(span=12, adjust=False).mean()
    ema_slow = close.ewm(span=48, adjust=False).mean()
    out["ema_distance"] = ema_fast / ema_slow - 1.0
    out["rsi_14"] = _rsi(close) / 100.0

    out["target_return"] = close.shift(-horizon_bars) / close - 1.0
    return out.replace([np.inf, -np.inf], np.nan)
