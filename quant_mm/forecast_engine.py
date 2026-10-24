from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.linear_model import ElasticNet, LinearRegression, LogisticRegression, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

FORECAST_FEATURES = [
    "ret_1", "ret_2", "ret_4", "ret_8", "ret_16", "ret_32",
    "vol_4", "vol_16", "vol_48",
    "volume_z_20", "volume_z_96",
    "range_pct", "body_pct",
    "ema_fast_distance", "ema_slow_distance", "rsi_14",
]


@dataclass(frozen=True)
class ForecastConfig:
    train_window: int = 5000
    min_train_rows: int = 700
    validation_fraction: float = 0.20
    neutral_threshold_bps: float = 8.0
    strong_threshold_bps: float = 25.0
    min_signal_confidence: float = 0.52
    random_state: int = 42
    use_advanced_models: bool = True
    realized_weight_blend: float = 0.65


@dataclass(frozen=True)
class ForecastResult:
    timeframe: str
    as_of: str
    current_price: float
    expected_return_pct: float
    expected_price: float
    lower_price: float
    upper_price: float
    up_probability: float
    confidence: float
    trend: str
    research_signal: str
    market_regime: str
    realized_volatility_pct: float
    validation_mae_pct: float
    validation_direction_accuracy: float
    model_agreement: float
    model_predictions_pct: dict[str, float]
    model_weights: dict[str, float]
    validation_model_weights: dict[str, float]
    weighting_mode: str
    training_rows: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi = rsi.mask((avg_loss == 0.0) & (avg_gain > 0.0), 100.0)
    rsi = rsi.mask((avg_loss == 0.0) & (avg_gain == 0.0), 50.0)
    return rsi


def build_forecast_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build causal features plus the next candle's close-return target."""
    out = df.copy().sort_index()
    close = out["close"].astype(float)
    open_ = out["open"].astype(float)
    volume = out["volume"].astype(float)

    for n in (1, 2, 4, 8, 16, 32):
        out[f"ret_{n}"] = close.pct_change(n)

    ret_1 = close.pct_change()
    for n in (4, 16, 48):
        out[f"vol_{n}"] = ret_1.rolling(n).std()

    for n in (20, 96):
        mean = volume.rolling(n).mean()
        std = volume.rolling(n).std().replace(0.0, np.nan)
        out[f"volume_z_{n}"] = (volume - mean) / std

    out["range_pct"] = (
        out["high"].astype(float) - out["low"].astype(float)
    ) / close.replace(0.0, np.nan)
    out["body_pct"] = (close - open_) / open_.replace(0.0, np.nan)

    ema_8 = close.ewm(span=8, adjust=False).mean()
    ema_32 = close.ewm(span=32, adjust=False).mean()
    ema_96 = close.ewm(span=96, adjust=False).mean()
    out["ema_fast_distance"] = ema_8 / ema_32 - 1.0
    out["ema_slow_distance"] = ema_32 / ema_96 - 1.0
    out["rsi_14"] = _rsi(close) / 100.0

    out["target_return"] = close.shift(-1) / close - 1.0
    return out.replace([np.inf, -np.inf], np.nan)


def _regression_models(random_state: int, use_advanced_models: bool = True):
    models = {
        "linear": make_pipeline(StandardScaler(), LinearRegression()),
        "ridge": make_pipeline(StandardScaler(), Ridge(alpha=3.0)),
        "hist_gb": HistGradientBoostingRegressor(
            max_depth=3,
            learning_rate=0.05,
            max_iter=180,
            l2_regularization=1.0,
            random_state=random_state,
        ),
    }
    if use_advanced_models:
        models.update(
            {
                "elastic_net": make_pipeline(
                    StandardScaler(),
                    ElasticNet(
                        alpha=1e-5,
                        l1_ratio=0.20,
                        max_iter=5000,
                        random_state=random_state,
                    ),
                ),
                "extra_trees": ExtraTreesRegressor(
                    n_estimators=180,
                    max_depth=9,
                    min_samples_leaf=8,
                    max_features=0.85,
                    n_jobs=-1,
                    random_state=random_state,
                ),
            }
        )
    return models


def _normalize_weights(values: dict[str, float], model_names: list[str]) -> dict[str, float]:
    clean = {name: max(0.0, float(values.get(name, 0.0))) for name in model_names}
    total = sum(clean.values())
    if total <= 0.0:
        equal = 1.0 / max(1, len(model_names))
        return {name: equal for name in model_names}
    return {name: value / total for name, value in clean.items()}


def _blend_weights(
    validation_weights: dict[str, float],
    realized_weights: dict[str, float] | None,
    blend: float,
) -> tuple[dict[str, float], str]:
    names = list(validation_weights)
    validation = _normalize_weights(validation_weights, names)
    if not realized_weights:
        return validation, "validation_only"

    realized = _normalize_weights(realized_weights, names)
    realized_coverage = sum(1 for name in names if float(realized_weights.get(name, 0.0)) > 0.0)
    if realized_coverage < 2:
        return validation, "validation_only"

    alpha = float(np.clip(blend, 0.0, 0.90))
    mixed = {
        name: (1.0 - alpha) * validation[name] + alpha * realized[name]
        for name in names
    }
    return _normalize_weights(mixed, names), "validation_plus_realized"


def _market_regime(latest: pd.Series) -> str:
    vol_16 = max(float(latest.get("vol_16", 0.0)), 1e-9)
    vol_48 = max(float(latest.get("vol_48", vol_16)), 1e-9)
    ret_16 = float(latest.get("ret_16", 0.0))
    volatility_ratio = vol_16 / vol_48
    trend_strength = abs(ret_16) / max(vol_16 * 4.0, 1e-9)

    if volatility_ratio >= 1.45:
        return "HIGH_VOL"
    if trend_strength >= 0.85 and ret_16 > 0.0:
        return "TREND_UP"
    if trend_strength >= 0.85 and ret_16 < 0.0:
        return "TREND_DOWN"
    return "RANGE"


def _classify_trend(expected_bps: float, up_probability: float, cfg: ForecastConfig) -> str:
    if expected_bps >= cfg.strong_threshold_bps and up_probability >= 0.62:
        return "STRONG_UP"
    if expected_bps >= cfg.neutral_threshold_bps and up_probability >= 0.55:
        return "UP"
    if expected_bps <= -cfg.strong_threshold_bps and up_probability <= 0.38:
        return "STRONG_DOWN"
    if expected_bps <= -cfg.neutral_threshold_bps and up_probability <= 0.45:
        return "DOWN"
    return "SIDEWAYS"


def _research_signal(trend: str, confidence: float, cfg: ForecastConfig) -> str:
    if confidence < cfg.min_signal_confidence:
        return "WAIT"
    if trend in {"UP", "STRONG_UP"}:
        return "LONG_BIAS"
    if trend in {"DOWN", "STRONG_DOWN"}:
        return "SHORT_BIAS"
    return "WAIT"


def forecast_next_candle(
    df: pd.DataFrame,
    timeframe: str,
    cfg: ForecastConfig | None = None,
    realized_model_weights: dict[str, float] | None = None,
) -> ForecastResult:
    """Forecast one next candle with chronological validation and optional live-score weights."""
    cfg = cfg or ForecastConfig()
    frame = build_forecast_features(df)
    latest = frame.iloc[-1]

    if latest[FORECAST_FEATURES].isna().any():
        raise ValueError("Not enough clean history to build the latest forecast features")

    train = frame.dropna(subset=FORECAST_FEATURES + ["target_return"]).tail(cfg.train_window)
    if len(train) < cfg.min_train_rows:
        raise ValueError(
            f"Need at least {cfg.min_train_rows} clean rows, got {len(train)} for {timeframe}"
        )

    val_size = max(100, int(len(train) * cfg.validation_fraction))
    val_size = min(val_size, max(100, len(train) - 300))
    fit = train.iloc[:-val_size]
    val = train.iloc[-val_size:]

    X_fit = fit[FORECAST_FEATURES]
    y_fit = fit["target_return"].astype(float)
    X_val = val[FORECAST_FEATURES]
    y_val = val["target_return"].astype(float)
    X_all = train[FORECAST_FEATURES]
    y_all = train["target_return"].astype(float)
    X_now = frame.iloc[[-1]][FORECAST_FEATURES]

    models = _regression_models(cfg.random_state, cfg.use_advanced_models)
    val_predictions: dict[str, np.ndarray] = {}
    maes: dict[str, float] = {}
    y_val_np = y_val.to_numpy()

    for name, model in models.items():
        model.fit(X_fit, y_fit)
        pred = np.asarray(model.predict(X_val), dtype=float)
        val_predictions[name] = pred
        maes[name] = float(np.mean(np.abs(y_val_np - pred)))

    inverse = {name: 1.0 / max(mae, 1e-8) for name, mae in maes.items()}
    validation_weights = _normalize_weights(inverse, list(models))
    weights, weighting_mode = _blend_weights(
        validation_weights,
        realized_model_weights,
        cfg.realized_weight_blend,
    )

    val_ensemble = sum(val_predictions[name] * weights[name] for name in models)
    residuals = y_val_np - val_ensemble
    lower_residual = float(np.quantile(residuals, 0.10))
    upper_residual = float(np.quantile(residuals, 0.90))
    direction_accuracy = float(np.mean(np.sign(val_ensemble) == np.sign(y_val_np)))
    validation_mae = float(np.mean(np.abs(residuals)))

    current_predictions: dict[str, float] = {}
    for name, model in models.items():
        model.fit(X_all, y_all)
        current_predictions[name] = float(model.predict(X_now)[0])

    expected_return = float(
        sum(current_predictions[name] * weights[name] for name in current_predictions)
    )

    y_direction = (y_all > 0.0).astype(int)
    if y_direction.nunique() < 2:
        up_probability = float(y_direction.iloc[-1])
    else:
        classifier = make_pipeline(
            StandardScaler(),
            LogisticRegression(C=0.7, max_iter=1000, random_state=cfg.random_state),
        )
        classifier.fit(X_all, y_direction)
        up_probability = float(classifier.predict_proba(X_now)[0, 1])

    pred_values = np.asarray(list(current_predictions.values()), dtype=float)
    residual_scale = max(float(np.std(residuals)), 1e-8)
    model_agreement = float(
        np.clip(1.0 - np.std(pred_values) / residual_scale, 0.0, 1.0)
    )
    directional_edge = float(np.clip((direction_accuracy - 0.5) * 2.0, 0.0, 1.0))
    probability_edge = float(np.clip(abs(up_probability - 0.5) * 2.0, 0.0, 1.0))
    confidence = float(
        np.clip(
            0.45 * probability_edge + 0.30 * directional_edge + 0.25 * model_agreement,
            0.0,
            1.0,
        )
    )

    current_price = float(latest["close"])
    expected_price = current_price * (1.0 + expected_return)
    lower_price = current_price * (1.0 + expected_return + lower_residual)
    upper_price = current_price * (1.0 + expected_return + upper_residual)
    lower_price, upper_price = sorted((max(0.0, lower_price), max(0.0, upper_price)))

    expected_bps = expected_return * 10_000.0
    trend = _classify_trend(expected_bps, up_probability, cfg)
    signal = _research_signal(trend, confidence, cfg)

    return ForecastResult(
        timeframe=timeframe,
        as_of=str(frame.index[-1]),
        current_price=current_price,
        expected_return_pct=expected_return * 100.0,
        expected_price=expected_price,
        lower_price=lower_price,
        upper_price=upper_price,
        up_probability=up_probability,
        confidence=confidence,
        trend=trend,
        research_signal=signal,
        market_regime=_market_regime(latest),
        realized_volatility_pct=float(latest["vol_16"] * 100.0),
        validation_mae_pct=validation_mae * 100.0,
        validation_direction_accuracy=direction_accuracy,
        model_agreement=model_agreement,
        model_predictions_pct={k: v * 100.0 for k, v in current_predictions.items()},
        model_weights=weights,
        validation_model_weights=validation_weights,
        weighting_mode=weighting_mode,
        training_rows=len(train),
    )
