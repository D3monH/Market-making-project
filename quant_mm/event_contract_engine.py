from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import numpy as np

from quant_mm.forecast_engine import ForecastResult
from quant_mm.institutional_alpha import AlphaResult


@dataclass(frozen=True)
class EventProbabilityResult:
    raw_classifier_up_probability: float
    regression_implied_up_probability: float
    model_up_probability: float
    alpha_adjustment_logit: float
    final_up_probability: float
    final_down_probability: float
    predicted_side: str
    research_signal: str
    confidence: float
    model_alpha_agreement: float
    alpha_used: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EventMarketEdge:
    up_price: float | None
    down_price: float | None
    up_ev_per_share: float | None
    down_ev_per_share: float | None
    up_roi_pct: float | None
    down_roi_pct: float | None
    best_side: str
    best_edge: float | None
    best_roi_pct: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clip_probability(value: float, eps: float = 1e-6) -> float:
    return float(np.clip(float(value), eps, 1.0 - eps))


def _logit(p: float) -> float:
    p = _clip_probability(p)
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def build_event_probability(
    forecast: ForecastResult,
    alpha: AlphaResult | None = None,
    *,
    classifier_weight: float = 0.70,
    max_alpha_logit_shift: float = 0.80,
) -> EventProbabilityResult:
    """Convert next-close forecast output into an event-contract probability.

    The event probability deliberately keeps model and institutional Alpha as
    separate evidence layers. Regression direction is converted into a bounded
    probability using recent volatility, then blended with the independent
    logistic classifier. Alpha can move the log-odds only within a strict cap,
    and high-volatility risk shrinks the final probability back toward 50%.
    """
    classifier_p = _clip_probability(forecast.up_probability)
    vol_pct = max(abs(float(forecast.realized_volatility_pct)), 0.05)
    regression_scale = max(vol_pct * 0.85, 0.05)
    regression_p = _sigmoid(float(forecast.expected_return_pct) / regression_scale)

    cw = float(np.clip(classifier_weight, 0.50, 0.90))
    model_p = _clip_probability(cw * classifier_p + (1.0 - cw) * regression_p)

    alpha_shift = 0.0
    risk_multiplier = 1.0
    alpha_used = alpha is not None
    if alpha is not None:
        evidence_strength = (
            float(alpha.composite_score)
            * float(alpha.consensus_confidence)
            * float(alpha.data_quality)
        )
        alpha_shift = float(
            np.clip(
                evidence_strength * max_alpha_logit_shift,
                -abs(max_alpha_logit_shift),
                abs(max_alpha_logit_shift),
            )
        )
        risk_multiplier = float(np.clip(alpha.risk_multiplier, 0.45, 1.0))

    alpha_p = _sigmoid(_logit(model_p) + alpha_shift)
    final_p = 0.5 + (alpha_p - 0.5) * risk_multiplier
    final_p = _clip_probability(final_p)

    model_sign = 1 if model_p >= 0.5 else -1
    alpha_sign = 0
    if alpha is not None:
        if alpha.composite_score > 0.05:
            alpha_sign = 1
        elif alpha.composite_score < -0.05:
            alpha_sign = -1
    agreement = 1.0 if alpha_sign == 0 else (1.0 if model_sign == alpha_sign else 0.0)

    distance = abs(final_p - 0.5) * 2.0
    forecast_quality = 0.55 * float(forecast.confidence) + 0.45 * float(forecast.validation_direction_accuracy)
    confidence = float(np.clip((0.45 + 0.55 * distance) * forecast_quality, 0.0, 1.0))

    if final_p >= 0.56:
        side = "UP"
    elif final_p <= 0.44:
        side = "DOWN"
    else:
        side = "WAIT"

    if side == "UP" and confidence >= 0.60:
        research_signal = "UP_EDGE"
    elif side == "DOWN" and confidence >= 0.60:
        research_signal = "DOWN_EDGE"
    else:
        research_signal = "WAIT"

    return EventProbabilityResult(
        raw_classifier_up_probability=classifier_p,
        regression_implied_up_probability=regression_p,
        model_up_probability=model_p,
        alpha_adjustment_logit=alpha_shift,
        final_up_probability=final_p,
        final_down_probability=1.0 - final_p,
        predicted_side=side,
        research_signal=research_signal,
        confidence=confidence,
        model_alpha_agreement=agreement,
        alpha_used=alpha_used,
    )


def _valid_market_price(value: float | None) -> float | None:
    if value is None:
        return None
    value = float(value)
    if 0.01 <= value <= 0.99:
        return value
    raise ValueError("Event contract market price must be between 0.01 and 0.99 USDT")


def calculate_market_edge(
    up_probability: float,
    *,
    up_price: float | None = None,
    down_price: float | None = None,
    min_edge: float = 0.02,
) -> EventMarketEdge:
    """Compare model probability with manually supplied Gate event prices.

    EV is gross settlement EV per share before dynamic Gate trading fees:
    P(win) * 1 USDT - entry price.
    """
    p_up = _clip_probability(up_probability)
    p_down = 1.0 - p_up
    up_price = _valid_market_price(up_price)
    down_price = _valid_market_price(down_price)

    up_ev = None if up_price is None else p_up - up_price
    down_ev = None if down_price is None else p_down - down_price
    up_roi = None if up_ev is None else up_ev / up_price * 100.0
    down_roi = None if down_ev is None else down_ev / down_price * 100.0

    candidates: list[tuple[str, float, float]] = []
    if up_ev is not None and up_roi is not None:
        candidates.append(("UP", up_ev, up_roi))
    if down_ev is not None and down_roi is not None:
        candidates.append(("DOWN", down_ev, down_roi))

    best_side = "WAIT"
    best_edge = None
    best_roi = None
    if candidates:
        side, edge, roi = max(candidates, key=lambda item: item[1])
        best_edge = edge
        best_roi = roi
        if edge >= float(min_edge):
            best_side = side

    return EventMarketEdge(
        up_price=up_price,
        down_price=down_price,
        up_ev_per_share=up_ev,
        down_ev_per_share=down_ev,
        up_roi_pct=up_roi,
        down_roi_pct=down_roi,
        best_side=best_side,
        best_edge=best_edge,
        best_roi_pct=best_roi,
    )
