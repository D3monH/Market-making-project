from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


@dataclass(frozen=True)
class LinearModelConfig:
    horizon_bars: int = 4
    train_window: int = 1200
    min_train_rows: int = 400
    retrain_every: int = 24


def _new_model() -> Pipeline:
    return Pipeline([
        ("scale", StandardScaler()),
        ("regression", LinearRegression()),
    ])


def walk_forward_predictions(
    frame: pd.DataFrame,
    feature_columns: list[str],
    cfg: LinearModelConfig,
) -> pd.Series:
    """Generate no-lookahead walk-forward predictions.

    At row t, only rows i whose i+horizon target is already observable by t are
    allowed into the training set.
    """
    predictions = pd.Series(np.nan, index=frame.index, name="predicted_return")
    model: Pipeline | None = None
    last_fit_pos = -10**9

    for pos in range(len(frame)):
        current_x = frame.iloc[[pos]][feature_columns]
        if current_x.isna().any(axis=None):
            continue

        train_end = pos - cfg.horizon_bars + 1
        if train_end <= 0:
            continue
        train_start = max(0, train_end - cfg.train_window)
        train = frame.iloc[train_start:train_end].dropna(
            subset=feature_columns + ["target_return"]
        )
        if len(train) < cfg.min_train_rows:
            continue

        if model is None or (pos - last_fit_pos) >= cfg.retrain_every:
            model = _new_model()
            model.fit(train[feature_columns], train["target_return"])
            last_fit_pos = pos

        predictions.iloc[pos] = float(model.predict(current_x)[0])

    return predictions
