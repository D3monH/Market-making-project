from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from quant_mm.forecast_store import load_forecast_records


def _json_map(value) -> dict[str, float]:
    if isinstance(value, dict):
        raw = value
    else:
        try:
            raw = json.loads(value or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
    result: dict[str, float] = {}
    for key, item in raw.items():
        try:
            result[str(key)] = float(item)
        except (TypeError, ValueError):
            continue
    return result


def model_performance_table(
    results_dir: str | Path,
    symbol: str,
    timeframe: str,
    candle_basis: str | None = None,
    limit: int = 250,
    half_life: float = 80.0,
) -> pd.DataFrame:
    """Evaluate each component model on real settled forecasts.

    Newer forecasts receive more weight through exponential decay. Metrics are
    computed only from true out-of-sample forecasts that were stored before the
    target candle closed.
    """
    records = load_forecast_records(results_dir, limit=max(1000, int(limit) * 8), symbol=symbol)
    if records.empty:
        return pd.DataFrame()

    frame = records.copy()
    frame = frame[(frame["status"] == "SETTLED") & (frame["timeframe"] == timeframe)]
    if candle_basis is not None and "candle_basis" in frame.columns:
        frame = frame[frame["candle_basis"] == candle_basis]
    frame = frame.dropna(subset=["actual_return_pct"]).head(max(1, int(limit)))
    if frame.empty:
        return pd.DataFrame()

    half_life = max(5.0, float(half_life))
    stats: dict[str, dict[str, float]] = {}

    for age, (_, row) in enumerate(frame.iterrows()):
        actual = float(row["actual_return_pct"])
        predictions = _json_map(row.get("model_predictions_json"))
        if not predictions:
            continue
        decay = float(0.5 ** (age / half_life))
        for model_name, predicted in predictions.items():
            entry = stats.setdefault(
                model_name,
                {
                    "weighted_error": 0.0,
                    "weighted_direction": 0.0,
                    "weight_sum": 0.0,
                    "samples": 0.0,
                },
            )
            entry["weighted_error"] += decay * abs(predicted - actual)
            entry["weighted_direction"] += decay * float(np.sign(predicted) == np.sign(actual))
            entry["weight_sum"] += decay
            entry["samples"] += 1.0

    rows: list[dict] = []
    for model_name, entry in stats.items():
        if entry["weight_sum"] <= 0.0:
            continue
        mae = entry["weighted_error"] / entry["weight_sum"]
        direction_accuracy = entry["weighted_direction"] / entry["weight_sum"]
        # Reward both direction and magnitude accuracy. The small floor prevents
        # a near-zero MAE on a tiny/easy sample from exploding the score.
        score = ((0.35 + 0.65 * direction_accuracy) ** 2) / max(mae + 0.05, 1e-6)
        rows.append(
            {
                "model": model_name,
                "samples": int(entry["samples"]),
                "direction_accuracy": float(direction_accuracy),
                "mae_pct": float(mae),
                "raw_score": float(score),
            }
        )

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows).sort_values(
        ["raw_score", "samples"], ascending=[False, False]
    ).reset_index(drop=True)
    total_score = float(result["raw_score"].sum())
    if total_score > 0.0:
        result["realized_weight"] = result["raw_score"] / total_score
    else:
        result["realized_weight"] = 1.0 / len(result)
    return result


def realized_model_weights(
    results_dir: str | Path,
    symbol: str,
    timeframe: str,
    candle_basis: str | None = None,
    min_samples: int = 30,
    limit: int = 250,
) -> dict[str, float]:
    """Return normalized real-world model weights after enough samples exist."""
    table = model_performance_table(
        results_dir,
        symbol=symbol,
        timeframe=timeframe,
        candle_basis=candle_basis,
        limit=limit,
    )
    if table.empty:
        return {}

    eligible = table[table["samples"] >= max(1, int(min_samples))].copy()
    if len(eligible) < 2:
        return {}

    total = float(eligible["realized_weight"].sum())
    if total <= 0.0:
        return {}
    return {
        str(row["model"]): float(row["realized_weight"]) / total
        for _, row in eligible.iterrows()
    }
