from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import yaml

from quant_mm.gate_stream import GatePublicStream, gate_symbol
from quant_mm.market_maker import MarketMakerConfig
from quant_mm.microstructure import MicroAlphaConfig
from quant_mm.online_model import OnlineLinearConfig
from quant_mm.paper_live import LivePaperConfig, LivePaperEngine


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def main() -> None:
    p = argparse.ArgumentParser(description="Gate real-time public-data paper market maker")
    p.add_argument("--config", default="config_gate.yaml")
    p.add_argument("--symbol", default=None, help="e.g. BTC/USDT")
    args = p.parse_args()

    cfg = load_config(args.config)
    symbol = args.symbol or cfg["symbol"]
    pair = gate_symbol(symbol)
    mm = MarketMakerConfig(**cfg["market_maker"])
    paper = LivePaperConfig(**cfg["paper"])
    micro = MicroAlphaConfig(**cfg["micro_alpha"])
    online = OnlineLinearConfig(**cfg["online_linear"])
    engine = LivePaperEngine(mm, paper, micro, online)
    out = Path(cfg["paths"]["live_dir"]) / pair
    last_print = 0.0

    def on_book(book):
        nonlocal last_print
        if book.symbol != pair:
            return
        engine.on_book(book)
        append_jsonl(out / "books.jsonl", book.__dict__)
        now = time.time()
        if now - last_print >= cfg["console"]["snapshot_every_seconds"]:
            s = engine.snapshot()
            print(
                f"[{pair}] eq={s['equity']:.2f} ret={s['return_pct']:+.3f}% "
                f"inv={s['inventory_usdt']:.2f} fills={s['fills']} "
                f"lin={s['linear_prediction_bps']:+.2f}bps n={s['linear_train_samples']} "
                f"halt={s['halted_reason']}"
            )
            append_jsonl(out / "snapshots.jsonl", s)
            last_print = now

    def on_trade(trade):
        if trade.symbol != pair:
            return
        append_jsonl(out / "trades_market.jsonl", trade.__dict__)
        fill = engine.on_trade(trade)
        if fill:
            append_jsonl(out / "fills_paper.jsonl", fill)
            print(f"[PAPER FILL] {fill['side']} {fill['qty']:.8f} @ {fill['price']:.8f}")

    print("=" * 72)
    print(f"Gate V0.2 real-time PAPER market making: {pair}")
    print("Public WebSocket only. No API key. No real orders.")
    print("Online linear model warms up from live book/trade observations.")
    print("Ctrl+C to stop.")
    print("=" * 72)
    GatePublicStream(ws_url=cfg["gate"]["ws_url"]).run_forever([symbol], on_book, on_trade)


if __name__ == "__main__":
    main()
