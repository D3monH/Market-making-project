from __future__ import annotations

import argparse
from datetime import datetime, timezone

import yaml

from quant_mm.dashboard_utils import (
    forecast_window,
    format_display_timestamp,
    save_forecast_run,
)
from quant_mm.data import fetch_closed_candle, fetch_forecast_ohlcv
from quant_mm.forecast_engine import ForecastConfig, forecast_next_candle
from quant_mm.forecast_store import settle_due_forecasts, store_status
from quant_mm.model_performance import realized_model_weights


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Multi-timeframe crypto trend forecast research tool")
    p.add_argument("--config", default="config_forecast.yaml")
    p.add_argument("--symbol", action="append", help="e.g. --symbol BTC/USDT")
    p.add_argument("--timeframe", action="append", help="e.g. --timeframe 15m --timeframe 1h")
    return p.parse_args()


def candle_basis_for(timeframe: str, daily_timezone: str) -> str:
    return (
        f"natural_day:{daily_timezone}"
        if timeframe == "1d"
        else "exchange_native_closed"
    )


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)

    exchange = cfg.get("exchange", "gate")
    symbols = args.symbol or cfg["symbols"]
    timeframes = args.timeframe or cfg["timeframes"]
    bars = int(cfg.get("bars", 5000))
    forecast_cfg = ForecastConfig(**cfg.get("forecast", {}))
    results_dir = cfg.get("results_dir", "results/forecast")
    time_cfg = cfg.get("time", {}) or {}
    daily_timezone = str(time_cfg.get("daily_candle_timezone", "Asia/Shanghai"))
    display_timezone = str(time_cfg.get("display_timezone", "Asia/Shanghai"))

    settlement = settle_due_forecasts(
        results_dir,
        candle_fetcher=fetch_closed_candle,
        limit=100,
    )
    if settlement["settled"]:
        print(
            f"Verified {settlement['settled']} matured forecasts "
            f"({settlement['remaining_due']} still due/unavailable)."
        )

    run_id = datetime.now(timezone.utc).isoformat()
    all_results: list[dict] = []
    print("=" * 96)
    print("Crypto Multi-Horizon Forecast Engine - research only")
    print("Models: Linear + Ridge + HistGradientBoosting + ElasticNet + ExtraTrees + Logistic direction")
    print(f"Display timezone: {display_timezone}")
    print(f"1d candle boundary: natural day in {daily_timezone}")
    print("=" * 96)

    for symbol in symbols:
        print(f"\n[{symbol}]")
        for timeframe in timeframes:
            try:
                data = fetch_forecast_ohlcv(
                    exchange,
                    symbol,
                    timeframe,
                    bars,
                    daily_timezone=daily_timezone,
                )
                candle_basis = candle_basis_for(timeframe, daily_timezone)
                live_weights = realized_model_weights(
                    results_dir,
                    symbol=symbol,
                    timeframe=timeframe,
                    candle_basis=candle_basis,
                    min_samples=forecast_cfg.realized_weight_min_samples,
                )
                result = forecast_next_candle(
                    data,
                    timeframe=timeframe,
                    cfg=forecast_cfg,
                    realized_model_weights=live_weights,
                )
                row = result.to_dict()
                row["symbol"] = symbol
                row["exchange"] = exchange
                row["forecast_run_at"] = run_id
                row["candle_basis"] = candle_basis
                row["realized_model_weights"] = live_weights
                all_results.append(row)

                window = forecast_window(result.as_of, timeframe)
                base_close_text = format_display_timestamp(
                    window["base_close_at"], display_timezone
                )
                target_close_text = format_display_timestamp(
                    window["target_close_at"], display_timezone
                )

                print(
                    f"  {timeframe:>4}  regime={result.market_regime:<10} "
                    f"trend={result.trend:<11} signal={result.research_signal:<10} "
                    f"E[r]={result.expected_return_pct:+.3f}%  "
                    f"P(up)={result.up_probability:.1%}  conf={result.confidence:.1%}"
                )
                print(
                    f"        base={result.current_price:.4f} -> target={result.expected_price:.4f}  "
                    f"weights={result.weighting_mode}"
                )
                print(
                    f"        base close={base_close_text} -> target close={target_close_text} "
                    f"range=[{result.lower_price:.4f}, {result.upper_price:.4f}]"
                )
            except Exception as exc:
                print(f"  {timeframe:>4}  ERROR: {exc}")

    latest_path, history_path = save_forecast_run(all_results, results_dir)
    status = store_status(results_dir)

    print(f"\nSaved latest:  {latest_path}")
    print(f"Saved history: {history_path}")
    print(f"Persistent DB: {status['db_path']}")
    print(
        f"Verification: total={status['total']} pending={status['pending']} "
        f"settled={status['settled']} due={status['due']}"
    )
    print("Note: forecast output is probabilistic research information, not guaranteed future price.")


if __name__ == "__main__":
    main()
