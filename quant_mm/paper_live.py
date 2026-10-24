from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass

import numpy as np

from .gate_stream import BookTicker, PublicTrade
from .market_maker import MarketMakerConfig, Quote, make_quote
from .microstructure import MicroAlphaConfig, RollingTradeFlow, micro_signal_bps
from .online_model import OnlineLinearConfig, OnlineMicroLinearModel


@dataclass(frozen=True)
class LivePaperConfig:
    initial_equity_usdt: float = 10000.0
    initial_base_inventory_usdt: float = 500.0
    target_inventory_ratio: float = 0.50
    maker_fee_bps: float = 2.0
    max_drawdown_pct: float = 0.15
    max_daily_loss_pct: float = 0.03
    min_quote_interval_ms: int = 500
    strict_crossing_fill: bool = True
    allow_short: bool = False
    vol_window: int = 60


class LivePaperEngine:
    def __init__(self, mm_cfg: MarketMakerConfig, paper_cfg: LivePaperConfig, micro_cfg: MicroAlphaConfig, online_cfg: OnlineLinearConfig):
        if paper_cfg.initial_base_inventory_usdt > paper_cfg.initial_equity_usdt:
            raise ValueError("initial_base_inventory_usdt cannot exceed initial_equity_usdt")
        self.mm_cfg = mm_cfg
        self.paper_cfg = paper_cfg
        self.micro_cfg = micro_cfg
        self.online_model = OnlineMicroLinearModel(online_cfg)
        self.cash = paper_cfg.initial_equity_usdt
        self.inventory_qty = 0.0
        self._portfolio_initialized = False
        self.peak_equity = paper_cfg.initial_equity_usdt
        self.day_start_equity = paper_cfg.initial_equity_usdt
        self.current_day = None
        self.halted_reason: str | None = None
        self.last_book: BookTicker | None = None
        self.quote: Quote | None = None
        self.bid_active = False
        self.ask_active = False
        self.last_quote_ms = 0
        self.mid_returns = deque(maxlen=paper_cfg.vol_window)
        self.prev_mid: float | None = None
        self.flow = RollingTradeFlow(maxlen=micro_cfg.trade_window)
        self.fills: list[dict] = []
        self.last_micro_signal_bps = 0.0
        self.last_linear_prediction = 0.0

    def _initialize_portfolio(self, mid: float) -> None:
        if self._portfolio_initialized:
            return
        base_value = self.paper_cfg.initial_base_inventory_usdt
        self.inventory_qty = base_value / mid if mid > 0 else 0.0
        self.cash -= base_value
        self._portfolio_initialized = True

    def _equity(self, mid: float) -> float:
        return self.cash + self.inventory_qty * mid

    def _update_risk(self, book: BookTicker) -> None:
        equity = self._equity(book.mid)
        self.peak_equity = max(self.peak_equity, equity)
        drawdown = 0.0 if self.peak_equity <= 0 else 1.0 - equity / self.peak_equity
        day = book.timestamp_ms // 86_400_000
        if self.current_day != day:
            self.current_day = day
            self.day_start_equity = equity
        daily_loss = 0.0 if self.day_start_equity <= 0 else 1.0 - equity / self.day_start_equity
        if drawdown >= self.paper_cfg.max_drawdown_pct:
            self.halted_reason = "max_drawdown"
        elif daily_loss >= self.paper_cfg.max_daily_loss_pct:
            self.halted_reason = "max_daily_loss"

    def _volatility(self) -> float:
        if len(self.mid_returns) < 10:
            return 0.0
        return float(np.std(self.mid_returns, ddof=1))

    def on_book(self, book: BookTicker) -> Quote | None:
        self.last_book = book
        self._initialize_portfolio(book.mid)
        if self.prev_mid and self.prev_mid > 0:
            self.mid_returns.append(book.mid / self.prev_mid - 1.0)
        self.prev_mid = book.mid
        volatility = self._volatility()
        self._update_risk(book)
        if self.halted_reason:
            self.bid_active = self.ask_active = False
            return None

        self.last_linear_prediction = self.online_model.observe(book, self.flow, volatility)
        if book.timestamp_ms - self.last_quote_ms < self.paper_cfg.min_quote_interval_ms:
            return self.quote

        self.last_micro_signal_bps = micro_signal_bps(book, self.flow, self.micro_cfg)
        warmup_overlay = 0.0 if self.online_model.fitted else self.last_micro_signal_bps / 10_000.0
        predicted_return = self.last_linear_prediction + warmup_overlay

        target_qty = (self.mm_cfg.max_inventory_usdt * self.paper_cfg.target_inventory_ratio) / book.mid
        inventory_deviation_qty = self.inventory_qty - target_qty
        self.quote = make_quote(book.mid, predicted_return, volatility, inventory_deviation_qty, self.mm_cfg)
        self.last_quote_ms = book.timestamp_ms

        inventory_value = self.inventory_qty * book.mid
        self.bid_active = inventory_value < self.mm_cfg.max_inventory_usdt
        self.ask_active = self.paper_cfg.allow_short or self.inventory_qty > 0
        return self.quote

    def on_trade(self, trade: PublicTrade) -> dict | None:
        self.flow.add(trade)
        if self.halted_reason or self.quote is None or self.last_book is None:
            return None
        if trade.side == "sell" and self.bid_active:
            crossed = trade.price < self.quote.bid if self.paper_cfg.strict_crossing_fill else trade.price <= self.quote.bid
            if crossed:
                self.bid_active = False
                return self._fill("buy", self.quote.bid, trade.timestamp_ms)
        elif trade.side == "buy" and self.ask_active:
            crossed = trade.price > self.quote.ask if self.paper_cfg.strict_crossing_fill else trade.price >= self.quote.ask
            if crossed:
                self.ask_active = False
                return self._fill("sell", self.quote.ask, trade.timestamp_ms)
        return None

    def _fill(self, side: str, price: float, timestamp_ms: int) -> dict | None:
        qty = self.mm_cfg.order_notional_usdt / price
        if side == "buy":
            max_qty = self.mm_cfg.max_inventory_usdt / price
            qty = min(qty, max(0.0, max_qty - self.inventory_qty))
            max_cash_qty = max(0.0, self.cash / (price * (1.0 + self.paper_cfg.maker_fee_bps / 10_000.0)))
            qty = min(qty, max_cash_qty)
        elif not self.paper_cfg.allow_short:
            qty = min(qty, max(0.0, self.inventory_qty))

        if qty <= 0:
            return None
        notional = qty * price
        fee = notional * self.paper_cfg.maker_fee_bps / 10_000.0
        if side == "buy":
            self.cash -= notional + fee
            self.inventory_qty += qty
        else:
            self.cash += notional - fee
            self.inventory_qty -= qty
        fill = {"timestamp_ms": timestamp_ms, "side": side, "qty": qty, "price": price, "notional": notional, "fee": fee}
        self.fills.append(fill)
        return fill

    def snapshot(self) -> dict:
        mid = self.last_book.mid if self.last_book else 0.0
        equity = self._equity(mid) if self.last_book else self.cash
        dd = 0.0 if self.peak_equity <= 0 else 1.0 - equity / self.peak_equity
        return {
            "cash": self.cash,
            "inventory_qty": self.inventory_qty,
            "inventory_usdt": self.inventory_qty * mid,
            "equity": equity,
            "return_pct": (equity / self.paper_cfg.initial_equity_usdt - 1.0) * 100.0,
            "drawdown_pct": dd * 100.0,
            "fills": len(self.fills),
            "micro_signal_bps": self.last_micro_signal_bps,
            "linear_prediction_bps": self.last_linear_prediction * 10_000.0,
            "linear_train_samples": self.online_model.train_samples,
            "linear_fitted": self.online_model.fitted,
            "halted_reason": self.halted_reason,
            "quote": asdict(self.quote) if self.quote else None,
        }
