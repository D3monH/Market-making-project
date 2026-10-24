from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable, Iterable


GATE_SPOT_WS = "wss://api.gateio.ws/ws/v4/"
GATE_SPOT_TESTNET_WS = "wss://ws-testnet.gate.com/v4/ws/spot"


def gate_symbol(symbol: str) -> str:
    return symbol.replace("/", "_").replace(":", "_").upper()


@dataclass(frozen=True)
class BookTicker:
    symbol: str
    bid: float
    bid_size: float
    ask: float
    ask_size: float
    timestamp_ms: int
    update_id: int | None = None

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass(frozen=True)
class PublicTrade:
    symbol: str
    side: str
    price: float
    amount: float
    timestamp_ms: int
    trade_id: str

    @property
    def notional(self) -> float:
        return self.price * self.amount


def parse_gate_message(message: str | dict) -> BookTicker | PublicTrade | None:
    data = json.loads(message) if isinstance(message, str) else message
    if data.get("event") != "update":
        return None
    channel = data.get("channel")
    result = data.get("result") or {}

    if channel == "spot.book_ticker":
        return BookTicker(
            symbol=str(result["s"]),
            bid=float(result["b"]),
            bid_size=float(result["B"]),
            ask=float(result["a"]),
            ask_size=float(result["A"]),
            timestamp_ms=int(result.get("t") or data.get("time_ms") or int(time.time() * 1000)),
            update_id=int(result["u"]) if result.get("u") is not None else None,
        )

    if channel == "spot.trades":
        ts = result.get("create_time_ms") or data.get("time_ms") or int(time.time() * 1000)
        return PublicTrade(
            symbol=str(result["currency_pair"]),
            side=str(result["side"]).lower(),
            price=float(result["price"]),
            amount=float(result["amount"]),
            timestamp_ms=int(float(ts)),
            trade_id=str(result.get("id", "")),
        )
    return None


class GatePublicStream:
    """Public Gate spot stream. No API key and no order placement."""

    def __init__(self, ws_url: str = GATE_SPOT_WS, reconnect_delay: float = 2.0):
        self.ws_url = ws_url
        self.reconnect_delay = reconnect_delay

    def run_forever(
        self,
        symbols: Iterable[str],
        on_book: Callable[[BookTicker], None],
        on_trade: Callable[[PublicTrade], None],
    ) -> None:
        import websocket

        pairs = [gate_symbol(s) for s in symbols]
        while True:
            ws = None
            try:
                ws = websocket.create_connection(self.ws_url, timeout=30)
                now = int(time.time())
                ws.send(json.dumps({"time": now, "channel": "spot.book_ticker", "event": "subscribe", "payload": pairs}))
                ws.send(json.dumps({"time": now, "channel": "spot.trades", "event": "subscribe", "payload": pairs}))
                while True:
                    event = parse_gate_message(ws.recv())
                    if isinstance(event, BookTicker):
                        on_book(event)
                    elif isinstance(event, PublicTrade):
                        on_trade(event)
            except KeyboardInterrupt:
                return
            except Exception as exc:
                print(f"[Gate WS] disconnected: {exc}; reconnecting...")
                time.sleep(self.reconnect_delay)
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass
