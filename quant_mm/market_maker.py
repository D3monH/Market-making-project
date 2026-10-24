from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class MarketMakerConfig:
    order_notional_usdt: float = 100.0
    max_inventory_usdt: float = 1000.0
    base_half_spread_bps: float = 4.0
    min_half_spread_bps: float = 2.0
    vol_spread_multiplier: float = 0.35
    signal_weight: float = 0.60
    max_signal_bps: float = 20.0
    inventory_skew_bps: float = 12.0


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    reservation_price: float
    half_spread_bps: float
    signal_bps: float
    inventory_ratio: float


def make_quote(
    mid_price: float,
    predicted_return: float,
    volatility: float,
    inventory_qty: float,
    cfg: MarketMakerConfig,
) -> Quote:
    if mid_price <= 0:
        raise ValueError("mid_price must be positive")

    max_inventory_qty = cfg.max_inventory_usdt / mid_price
    inventory_ratio = 0.0
    if max_inventory_qty > 0:
        inventory_ratio = float(np.clip(inventory_qty / max_inventory_qty, -1.0, 1.0))

    raw_signal_bps = predicted_return * 10_000.0 * cfg.signal_weight
    signal_bps = float(np.clip(raw_signal_bps, -cfg.max_signal_bps, cfg.max_signal_bps))

    inventory_shift_bps = inventory_ratio * cfg.inventory_skew_bps
    reservation_price = mid_price * (
        1.0 + (signal_bps - inventory_shift_bps) / 10_000.0
    )

    vol_bps = max(float(volatility), 0.0) * 10_000.0
    half_spread_bps = max(
        cfg.min_half_spread_bps,
        cfg.base_half_spread_bps + vol_bps * cfg.vol_spread_multiplier,
    )
    half_spread = half_spread_bps / 10_000.0

    bid = reservation_price * (1.0 - half_spread)
    ask = reservation_price * (1.0 + half_spread)
    return Quote(
        bid=float(bid),
        ask=float(ask),
        reservation_price=float(reservation_price),
        half_spread_bps=float(half_spread_bps),
        signal_bps=signal_bps,
        inventory_ratio=inventory_ratio,
    )
