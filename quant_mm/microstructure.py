from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .gate_stream import BookTicker, PublicTrade


@dataclass(frozen=True)
class MicroAlphaConfig:
    book_weight_bps: float = 4.0
    trade_flow_weight_bps: float = 4.0
    max_micro_signal_bps: float = 8.0
    trade_window: int = 100


def book_imbalance(book: BookTicker) -> float:
    total = book.bid_size + book.ask_size
    return 0.0 if total <= 0 else (book.bid_size - book.ask_size) / total


class RollingTradeFlow:
    def __init__(self, maxlen: int = 100):
        self._signed = deque(maxlen=maxlen)
        self._abs = deque(maxlen=maxlen)

    def add(self, trade: PublicTrade) -> None:
        signed = trade.notional if trade.side == "buy" else -trade.notional
        self._signed.append(signed)
        self._abs.append(abs(trade.notional))

    @property
    def imbalance(self) -> float:
        denom = sum(self._abs)
        return 0.0 if denom <= 0 else sum(self._signed) / denom


def micro_signal_bps(book: BookTicker, flow: RollingTradeFlow, cfg: MicroAlphaConfig) -> float:
    raw = book_imbalance(book) * cfg.book_weight_bps + flow.imbalance * cfg.trade_flow_weight_bps
    return max(-cfg.max_micro_signal_bps, min(cfg.max_micro_signal_bps, raw))
