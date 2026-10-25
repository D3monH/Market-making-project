from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DirectionView:
    direction_code: str
    direction_label: str
    arrow: str
    direction_probability: float
    expected_return_pct: float
    price_change: float
    quote_asset: str
    strength_label: str
    probability_conflict: bool


def build_direction_view(
    *,
    symbol: str,
    base_price: float,
    expected_price: float,
    expected_return_pct: float,
    up_probability: float,
    neutral_threshold_bps: float = 8.0,
    strong_threshold_bps: float = 25.0,
) -> DirectionView:
    """Build a human-facing next-candle direction summary.

    Direction is defined by the model's predicted next close relative to the
    previous fully closed candle close. The classification probability remains
    independent, so disagreement between regression direction and the
    up/down classifier is surfaced rather than hidden.
    """

    expected_return_pct = float(expected_return_pct)
    up_probability = min(1.0, max(0.0, float(up_probability)))
    base_price = float(base_price)
    expected_price = float(expected_price)

    if expected_return_pct > 0.0:
        code = "UP"
        label = "收涨（多）"
        arrow = "↑"
        direction_probability = up_probability
        probability_conflict = up_probability < 0.5
    elif expected_return_pct < 0.0:
        code = "DOWN"
        label = "收跌（空）"
        arrow = "↓"
        direction_probability = 1.0 - up_probability
        probability_conflict = up_probability > 0.5
    else:
        code = "FLAT"
        label = "基本持平"
        arrow = "→"
        direction_probability = max(up_probability, 1.0 - up_probability)
        probability_conflict = False

    abs_bps = abs(expected_return_pct) * 100.0
    if abs_bps >= float(strong_threshold_bps):
        strength = "强"
    elif abs_bps >= float(neutral_threshold_bps):
        strength = "一般"
    else:
        strength = "弱 / 接近震荡"

    quote_asset = symbol.split("/")[-1] if "/" in symbol else "计价币"

    return DirectionView(
        direction_code=code,
        direction_label=label,
        arrow=arrow,
        direction_probability=direction_probability,
        expected_return_pct=expected_return_pct,
        price_change=expected_price - base_price,
        quote_asset=quote_asset,
        strength_label=strength,
        probability_conflict=probability_conflict,
    )
