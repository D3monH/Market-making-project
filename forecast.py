from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from quant_mm.data import fetch_ohlcv
from quant_mm.forecast_engine import ForecastConfig, forecast_next_candle


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Multi-timeframe crypto trend forecast research tool")
    p.add_argument("--config", default="config_forecast.yaml")
    p.add_argument("--symbol", action="append", help="e.g. --symbol BTC/USDT")
    p.add_argument("--timeframe", action="append", help="e.g. --timeframe 15m --timeframe 1h")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)

    exchange = cfg.get("exchange", "gateio")
    symbols = args.symbol or cfg["symbols"]
    timeframes = args.timeframe or cfg["timeframes"]
    bars = int(cfg.get("bars", 5000))
    forecast_cfg = ForecastConfig(**cfg.get("forecast", {}))

    run_id = datetime.now(timezone.utc).isoformat()
    all_results: list[dict] = []
    print("=" * 88)
    print("Crypto Multi-Horizon Forecast Engine V0.3 - research only")
    print("Models: LinearRegression + Ridge + HistGradientBoosting + LogisticRegression")
    print("=" * 88)

    for symbol in symbols:
        print(f"\n[{symbol}]")
        for timeframe in timeframes:
            try:
                data = fetch_ohlcv(exchange, symbol, timeframe, bars)
                result = forecast_next_candle(data, timeframe=timeframe, cfg=forecast_cfg)
                row = result.to_dict()
                row["symbol"] = symbol
                row["exchange"] = exchange
                row["forecast_run_at"] = run_id
                all_results.append(row)

                print(
                    f"  {timeframe:>4}  trend={result.trend:<11} "
                    f"signal={result.research_signal:<10} "
                    f"E[r]={result.expected_return_pct:+.3f}%  "
                    f"P(up)={result.up_probability:.1%}  "
                    f"conf={result.confidence:.1%}  "
                    f"target={result.expected_price:.4f}  "
                    f"range=[{result.lower_price:.4f}, {result.upper_price:.4f}]"
                )
            except Exception as exc:
                print(f"  {timeframe:>4}  ERROR: {exc}")

    out_dir = Path(cfg.get("results_dir", "results/forecast"))
    out_dir.mkdir(parents=True, exist_ok=True)

    latest_path = out_dir / "latest_forecast.json"
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    history_path = out_dir / "forecast_history.jsonl"
    with open(history_path, "a", encoding="utf-8") as f:
        for row in all_results:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\nSaved latest:  {latest_path}")
    print(f"Saved history: {history_path}")
    print("Note: forecast output is probabilistic research information, not guaranteed future price.")


if __name__ == "__main__":
    main()
