from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import numpy as np
import pandas as pd
import requests

GATE_API = "https://api.gateio.ws/api/v4"


@dataclass(frozen=True)
class AlphaResult:
    composite_score: float
    risk_multiplier: float
    consensus_score: float
    consensus_signal: str
    consensus_confidence: float
    factor_scores: dict[str, float]
    factor_weights: dict[str, float]
    raw_context: dict[str, Any]
    data_quality: float
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return float(np.clip(float(value), low, high))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        if math.isfinite(result):
            return result
    except (TypeError, ValueError):
        pass
    return float(default)


def _symbol_to_contract(symbol: str) -> str:
    return symbol.replace("/", "_").replace(":USDT", "").upper()


def _gate_stats_interval(timeframe: str) -> str:
    tf = str(timeframe).lower()
    supported = {"5m", "15m", "30m", "1h", "4h", "8h", "1d", "3d", "7d"}
    if tf in supported:
        return tf
    if tf == "12h":
        return "4h"
    if tf in {"1m", "3m"}:
        return "5m"
    return "1h"


def _get_json(path: str, params: dict[str, Any] | None = None, timeout: float = 8.0):
    response = requests.get(
        GATE_API + path,
        params=params or {},
        headers={"Accept": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def fetch_gate_derivatives_context(
    symbol: str,
    timeframe: str,
    history_points: int = 96,
) -> dict[str, Any]:
    """Fetch public Gate derivatives context; no API key is required.

    This layer is deliberately kept separate from the price forecast model. It is
    used as an independent alpha/risk confirmation signal so that derivatives
    data cannot silently contaminate historical model validation before we have
    accumulated enough aligned out-of-sample observations.
    """
    contract = _symbol_to_contract(symbol)
    interval = _gate_stats_interval(timeframe)
    context: dict[str, Any] = {
        "available": False,
        "contract": contract,
        "interval": interval,
        "errors": [],
    }

    try:
        tickers = _get_json(
            "/futures/usdt/tickers",
            {"contract": contract},
        )
        ticker = tickers[0] if isinstance(tickers, list) and tickers else {}
        context["ticker"] = ticker
    except Exception as exc:  # network/API failures should not kill forecasts
        context["ticker"] = {}
        context["errors"].append(f"ticker: {exc}")

    try:
        stats = _get_json(
            "/futures/usdt/contract_stats",
            {"contract": contract, "interval": interval, "limit": int(history_points)},
        )
        context["stats"] = stats if isinstance(stats, list) else []
    except Exception as exc:
        context["stats"] = []
        context["errors"].append(f"contract_stats: {exc}")

    try:
        funding = _get_json(
            "/futures/usdt/funding_rate",
            {"contract": contract, "limit": min(max(int(history_points), 20), 1000)},
        )
        context["funding"] = funding if isinstance(funding, list) else []
    except Exception as exc:
        context["funding"] = []
        context["errors"].append(f"funding: {exc}")

    context["available"] = bool(context.get("ticker") or context.get("stats") or context.get("funding"))
    return context


def _robust_z(values: list[float], current: float | None = None) -> float:
    clean = np.asarray([v for v in values if math.isfinite(v)], dtype=float)
    if clean.size < 5:
        return 0.0
    x = float(clean[-1] if current is None else current)
    median = float(np.median(clean))
    mad = float(np.median(np.abs(clean - median)))
    scale = max(mad * 1.4826, float(np.std(clean)), 1e-12)
    return (x - median) / scale


def _price_factors(market: pd.DataFrame) -> tuple[dict[str, float], dict[str, Any]]:
    if market.empty or len(market) < 100:
        raise ValueError("Institutional alpha needs at least 100 closed candles")

    close = market["close"].astype(float)
    high = market["high"].astype(float)
    low = market["low"].astype(float)
    ret = close.pct_change()

    r8 = float(close.iloc[-1] / close.iloc[-9] - 1.0)
    r32 = float(close.iloc[-1] / close.iloc[-33] - 1.0)
    vol16 = max(float(ret.tail(16).std()), 1e-8)
    vol96 = max(float(ret.tail(96).std()), 1e-8)
    trend_raw = 0.65 * r8 / (vol16 * math.sqrt(8.0)) + 0.35 * r32 / (vol96 * math.sqrt(32.0))
    momentum = math.tanh(trend_raw / 2.0)

    ema16 = float(close.ewm(span=16, adjust=False).mean().iloc[-1])
    ema64 = float(close.ewm(span=64, adjust=False).mean().iloc[-1])
    trend = math.tanh(((ema16 / max(ema64, 1e-12)) - 1.0) / max(vol96 * 2.0, 1e-8))

    mean32 = float(close.tail(32).mean())
    std32 = max(float(close.tail(32).std()), 1e-8)
    price_z = (float(close.iloc[-1]) - mean32) / std32
    mean_reversion = -math.tanh(price_z / 2.0)

    current_vol = float(ret.tail(16).std())
    historical_vol = ret.rolling(16).std().dropna().tail(256)
    vol_z = _robust_z(historical_vol.tolist(), current_vol)
    risk_multiplier = float(np.clip(1.0 - max(0.0, vol_z) * 0.12, 0.45, 1.0))

    range_pct = float(((high - low) / close.replace(0.0, np.nan)).tail(16).mean())
    context = {
        "return_8": r8,
        "return_32": r32,
        "volatility_16": current_vol,
        "volatility_z": vol_z,
        "range_pct_16": range_pct,
        "price_z_32": price_z,
    }
    return {
        "momentum": _clip(momentum),
        "trend": _clip(trend),
        "mean_reversion": _clip(mean_reversion),
    }, {"risk_multiplier": risk_multiplier, **context}


def _derivatives_factors(context: dict[str, Any], momentum_score: float) -> tuple[dict[str, float], dict[str, Any], float]:
    ticker = context.get("ticker") or {}
    stats = context.get("stats") or []
    funding = context.get("funding") or []

    factor_scores: dict[str, float] = {}
    raw: dict[str, Any] = {}
    available = 0
    expected = 5

    funding_values = [_safe_float(row.get("r"), float("nan")) for row in funding if isinstance(row, dict)]
    funding_values = [v for v in funding_values if math.isfinite(v)]
    current_funding = _safe_float(ticker.get("funding_rate"), funding_values[-1] if funding_values else 0.0)
    if funding_values or ticker.get("funding_rate") is not None:
        funding_z = _robust_z(funding_values, current_funding) if funding_values else current_funding / 0.0005
        # Crowded positive funding is treated as a contrarian headwind for next-price direction.
        factor_scores["carry_crowding"] = _clip(-math.tanh(funding_z / 2.0))
        raw["funding_rate"] = current_funding
        raw["funding_z"] = funding_z
        available += 1

    stat_rows = [row for row in stats if isinstance(row, dict)]
    oi_values = [_safe_float(row.get("open_interest_usd"), float("nan")) for row in stat_rows]
    oi_values = [v for v in oi_values if math.isfinite(v) and v > 0]
    if len(oi_values) >= 4:
        lookback = oi_values[-min(8, len(oi_values))]
        oi_change = oi_values[-1] / max(lookback, 1e-12) - 1.0
        # OI is not inherently bullish/bearish; rising OI confirms the prevailing price trend.
        factor_scores["open_interest"] = _clip(math.tanh(oi_change * 8.0) * np.sign(momentum_score))
        raw["open_interest_usd"] = oi_values[-1]
        raw["open_interest_change"] = oi_change
        available += 1

    last = _safe_float(ticker.get("last"), 0.0)
    index_price = _safe_float(ticker.get("index_price"), 0.0)
    mark_price = _safe_float(ticker.get("mark_price"), 0.0)
    ref_price = index_price if index_price > 0 else mark_price
    if last > 0 and ref_price > 0:
        premium = last / ref_price - 1.0
        factor_scores["basis_crowding"] = _clip(-math.tanh(premium / 0.0025))
        raw["perp_premium"] = premium
        raw["futures_last"] = last
        raw["index_price"] = index_price
        raw["mark_price"] = mark_price
        available += 1

    if stat_rows:
        last_stat = stat_rows[-1]
        lsr_account = _safe_float(last_stat.get("lsr_account"), 1.0)
        if lsr_account > 0:
            crowd = math.log(lsr_account)
            factor_scores["positioning"] = _clip(-math.tanh(crowd / 0.35))
            raw["long_short_account_ratio"] = lsr_account
            available += 1

    bid_size = _safe_float(ticker.get("highest_size"), 0.0)
    ask_size = _safe_float(ticker.get("lowest_size"), 0.0)
    if bid_size > 0 or ask_size > 0:
        imbalance = (bid_size - ask_size) / max(bid_size + ask_size, 1e-12)
        factor_scores["microstructure"] = _clip(imbalance)
        raw["top_book_imbalance"] = imbalance
        available += 1

    return factor_scores, raw, available / expected


def build_institutional_alpha(
    market: pd.DataFrame,
    *,
    expected_return_pct: float,
    model_confidence: float,
    derivatives_context: dict[str, Any] | None = None,
    market_regime: str = "",
) -> AlphaResult:
    """Combine independent institutional-style alpha confirmations.

    The model's price forecast remains untouched. This layer produces a separate
    consensus signal and risk-adjusted confidence, following a research pattern
    of independent signals + portfolio/risk overlay rather than a single opaque
    forecast model.
    """
    price_scores, price_raw = _price_factors(market)
    deriv_scores: dict[str, float] = {}
    deriv_raw: dict[str, Any] = {}
    derivative_quality = 0.0
    if derivatives_context:
        deriv_scores, deriv_raw, derivative_quality = _derivatives_factors(
            derivatives_context,
            price_scores["momentum"],
        )

    scores = {**price_scores, **deriv_scores}
    base_weights = {
        "momentum": 0.24,
        "trend": 0.20,
        "mean_reversion": 0.10,
        "carry_crowding": 0.13,
        "open_interest": 0.11,
        "basis_crowding": 0.08,
        "positioning": 0.07,
        "microstructure": 0.07,
    }

    # Regime-aware re-weighting: trend regimes favor momentum/trend; ranges favor mean reversion.
    weights = dict(base_weights)
    if market_regime in {"TREND_UP", "TREND_DOWN"}:
        weights["momentum"] *= 1.25
        weights["trend"] *= 1.25
        weights["mean_reversion"] *= 0.55
    elif market_regime == "RANGE":
        weights["mean_reversion"] *= 1.75
        weights["momentum"] *= 0.75
        weights["trend"] *= 0.70
    elif market_regime == "HIGH_VOL":
        weights["microstructure"] *= 1.20
        weights["momentum"] *= 0.85

    active_weights = {name: weight for name, weight in weights.items() if name in scores}
    total_weight = sum(active_weights.values()) or 1.0
    normalized_weights = {name: weight / total_weight for name, weight in active_weights.items()}
    composite = float(sum(scores[name] * normalized_weights[name] for name in normalized_weights))

    model_edge = math.tanh(float(expected_return_pct) / 0.35)
    consensus = _clip(0.65 * model_edge + 0.35 * composite)
    risk_multiplier = float(price_raw["risk_multiplier"])

    agreement = 1.0 - min(1.0, abs(model_edge - composite) / 2.0)
    consensus_confidence = float(
        np.clip(
            float(model_confidence) * (0.65 + 0.35 * agreement) * risk_multiplier,
            0.0,
            1.0,
        )
    )

    if consensus >= 0.55 and consensus_confidence >= 0.55:
        signal = "STRONG_LONG_BIAS"
    elif consensus >= 0.18 and consensus_confidence >= 0.45:
        signal = "LONG_BIAS"
    elif consensus <= -0.55 and consensus_confidence >= 0.55:
        signal = "STRONG_SHORT_BIAS"
    elif consensus <= -0.18 and consensus_confidence >= 0.45:
        signal = "SHORT_BIAS"
    else:
        signal = "WAIT"

    price_quality = 1.0
    data_quality = float(np.clip(0.65 * price_quality + 0.35 * derivative_quality, 0.0, 1.0))
    raw_context = {
        "price": price_raw,
        "derivatives": deriv_raw,
        "derivatives_errors": list((derivatives_context or {}).get("errors", [])),
    }
    return AlphaResult(
        composite_score=_clip(composite),
        risk_multiplier=risk_multiplier,
        consensus_score=consensus,
        consensus_signal=signal,
        consensus_confidence=consensus_confidence,
        factor_scores={k: _clip(v) for k, v in scores.items()},
        factor_weights=normalized_weights,
        raw_context=raw_context,
        data_quality=data_quality,
        source="price+gate_public_derivatives" if derivatives_context else "price_only",
    )
