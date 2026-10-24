from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from quant_mm.backtest import BacktestConfig, run_backtest
from quant_mm.data import cache_path, fetch_ohlcv, load_csv, save_cache
from quant_mm.features import FEATURE_COLUMNS, build_features
from quant_mm.market_maker import MarketMakerConfig
from quant_mm.model import LinearModelConfig, walk_forward_predictions


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def safe_name(symbol: str) -> str:
    return symbol.replace("/", "_").replace(":", "_")


def run_symbol(symbol: str, cfg: dict, csv_path: str | None = None) -> dict:
    exchange = cfg["exchange"]
    timeframe = cfg["timeframe"]
    bars = int(cfg["bars"])

    if csv_path:
        raw = load_csv(csv_path)
    else:
        raw = fetch_ohlcv(exchange, symbol, timeframe, bars)
        path = cache_path(cfg["paths"]["cache_dir"], exchange, symbol, timeframe)
        save_cache(raw, path)

    model_cfg = LinearModelConfig(**cfg["model"])
    mm_cfg = MarketMakerConfig(**cfg["market_maker"])
    bt_cfg = BacktestConfig(**cfg["backtest"])

    frame = build_features(raw, horizon_bars=model_cfg.horizon_bars)
    frame["predicted_return"] = walk_forward_predictions(frame, FEATURE_COLUMNS, model_cfg)

    equity, trades, summary = run_backtest(frame, mm_cfg, bt_cfg, timeframe)
    summary.update({
        "symbol": symbol,
        "exchange": exchange,
        "timeframe": timeframe,
        "bars_loaded": int(len(raw)),
        "prediction_rows": int(frame["predicted_return"].notna().sum()),
    })

    out_dir = Path(cfg["paths"]["results_dir"]) / safe_name(symbol)
    out_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out_dir / "features_predictions.csv")
    equity.to_csv(out_dir / "equity_curve.csv")
    trades.to_csv(out_dir / "trades.csv", index=False)
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return summary


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Linear-regression-guided crypto market-making research simulator"
    )
    p.add_argument("--config", default="config.yaml", help="YAML configuration path")
    p.add_argument("--symbol", action="append", help="Override symbol(s), e.g. --symbol BTC/USDT")
    p.add_argument("--csv", help="Use one local OHLCV CSV instead of downloading")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    symbols = args.symbol or cfg["symbols"]
    if args.csv and len(symbols) != 1:
        raise SystemExit("--csv mode requires exactly one --symbol")

    print("=" * 72)
    print("Crypto Market Making V0.1 - research / backtest only")
    print("No real-money orders are placed by this program.")
    print("=" * 72)

    for symbol in symbols:
        print(f"\n[RUN] {symbol} {cfg['timeframe']} on {cfg['exchange']}")
        summary = run_symbol(symbol, cfg, csv_path=args.csv)
        print(
            f"[RESULT] return={summary['total_return_pct']:.2f}%  "
            f"maxDD={summary['max_drawdown_pct']:.2f}%  "
            f"Sharpe={summary['sharpe']:.2f}  fills={summary['fills']}"
        )

    print("\nDone. Detailed CSV/JSON files are under results/.")


if __name__ == "__main__":
    main()
