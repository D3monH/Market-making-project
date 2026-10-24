from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import re

import numpy as np
import pandas as pd

from .market_maker import MarketMakerConfig, make_quote


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash_usdt: float = 10_000.0
    maker_fee_bps: float = 2.0
    max_drawdown_pct: float = 0.15


def _periods_per_year(timeframe: str) -> float:
    match = re.fullmatch(r"(\d+)([mhd])", timeframe.strip().lower())
    if not match:
        return 365.0
    n = int(match.group(1))
    unit = match.group(2)
    seconds = {"m": 60, "h": 3600, "d": 86400}[unit] * n
    return 365.0 * 24.0 * 3600.0 / seconds


def run_backtest(
    frame: pd.DataFrame,
    mm_cfg: MarketMakerConfig,
    bt_cfg: BacktestConfig,
    timeframe: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Candle-level passive-fill simulator.

    Quote at candle t close; only candle t+1 can fill it. OHLCV does not reveal
    intrabar event order or queue position, so this is intentionally a research
    approximation rather than a live-execution claim.
    """
    cash = float(bt_cfg.initial_cash_usdt)
    inventory = 0.0
    fee_rate = bt_cfg.maker_fee_bps / 10_000.0
    fees_paid = 0.0
    peak_equity = cash
    stopped = False

    equity_rows: list[dict] = []
    trades: list[dict] = []
    first_active_close: float | None = None

    for pos in range(len(frame) - 1):
        row = frame.iloc[pos]
        nxt = frame.iloc[pos + 1]
        next_timestamp = frame.index[pos + 1]

        pred = row.get("predicted_return", np.nan)
        vol = row.get("volatility_12", np.nan)
        close = float(row["close"])
        next_close = float(nxt["close"])

        if pd.isna(pred) or pd.isna(vol) or stopped:
            equity = cash + inventory * next_close
            peak_equity = max(peak_equity, equity)
            drawdown = 0.0 if peak_equity <= 0 else equity / peak_equity - 1.0
            equity_rows.append({
                "timestamp": next_timestamp,
                "equity": equity,
                "cash": cash,
                "inventory_qty": inventory,
                "drawdown": drawdown,
                "bid": np.nan,
                "ask": np.nan,
                "predicted_return": pred,
            })
            continue

        if first_active_close is None:
            first_active_close = close

        quote = make_quote(close, float(pred), float(vol), inventory, mm_cfg)
        max_inventory_qty = mm_cfg.max_inventory_usdt / close
        buy_qty = mm_cfg.order_notional_usdt / quote.bid
        sell_qty = mm_cfg.order_notional_usdt / quote.ask

        buy_touched = float(nxt["low"]) <= quote.bid
        sell_touched = float(nxt["high"]) >= quote.ask

        if buy_touched and inventory + buy_qty <= max_inventory_qty:
            notional = quote.bid * buy_qty
            fee = notional * fee_rate
            cash -= notional + fee
            inventory += buy_qty
            fees_paid += fee
            trades.append({
                "timestamp": next_timestamp,
                "side": "buy",
                "price": quote.bid,
                "qty": buy_qty,
                "notional": notional,
                "fee": fee,
                "signal_bps": quote.signal_bps,
                "half_spread_bps": quote.half_spread_bps,
            })

        if sell_touched and inventory - sell_qty >= -max_inventory_qty:
            notional = quote.ask * sell_qty
            fee = notional * fee_rate
            cash += notional - fee
            inventory -= sell_qty
            fees_paid += fee
            trades.append({
                "timestamp": next_timestamp,
                "side": "sell",
                "price": quote.ask,
                "qty": sell_qty,
                "notional": notional,
                "fee": fee,
                "signal_bps": quote.signal_bps,
                "half_spread_bps": quote.half_spread_bps,
            })

        equity = cash + inventory * next_close
        peak_equity = max(peak_equity, equity)
        drawdown = 0.0 if peak_equity <= 0 else equity / peak_equity - 1.0
        if drawdown <= -abs(bt_cfg.max_drawdown_pct):
            stopped = True

        equity_rows.append({
            "timestamp": next_timestamp,
            "equity": equity,
            "cash": cash,
            "inventory_qty": inventory,
            "drawdown": drawdown,
            "bid": quote.bid,
            "ask": quote.ask,
            "reservation_price": quote.reservation_price,
            "signal_bps": quote.signal_bps,
            "half_spread_bps": quote.half_spread_bps,
            "predicted_return": pred,
        })

    equity_df = pd.DataFrame(equity_rows).set_index("timestamp") if equity_rows else pd.DataFrame()
    trades_df = pd.DataFrame(trades)

    if equity_df.empty:
        summary = {
            "initial_equity": bt_cfg.initial_cash_usdt,
            "final_equity": bt_cfg.initial_cash_usdt,
            "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe": 0.0,
            "fills": 0,
            "fees_paid_usdt": 0.0,
            "buy_hold_return_pct": 0.0,
            "stopped_by_drawdown": False,
        }
        return equity_df, trades_df, summary

    returns = equity_df["equity"].pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    sharpe = 0.0
    if len(returns) > 1 and returns.std(ddof=1) > 0:
        sharpe = float(returns.mean() / returns.std(ddof=1) * math.sqrt(_periods_per_year(timeframe)))

    final_equity = float(equity_df["equity"].iloc[-1])
    total_return = final_equity / bt_cfg.initial_cash_usdt - 1.0
    max_drawdown = float(equity_df["drawdown"].min())

    buy_hold = 0.0
    if first_active_close and first_active_close > 0:
        buy_hold = float(frame["close"].iloc[-1] / first_active_close - 1.0)

    summary = {
        "initial_equity": bt_cfg.initial_cash_usdt,
        "final_equity": round(final_equity, 6),
        "total_return_pct": round(total_return * 100.0, 4),
        "max_drawdown_pct": round(max_drawdown * 100.0, 4),
        "sharpe": round(sharpe, 4),
        "fills": int(len(trades_df)),
        "fees_paid_usdt": round(fees_paid, 6),
        "final_inventory_qty": round(inventory, 10),
        "buy_hold_return_pct": round(buy_hold * 100.0, 4),
        "stopped_by_drawdown": bool(stopped),
        "market_maker_config": asdict(mm_cfg),
        "backtest_config": asdict(bt_cfg),
    }
    return equity_df, trades_df, summary
