from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

import yaml

from quant_mm.data import fetch_closed_candle
from quant_mm.forecast_store import settle_due_forecasts, store_status


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Continuously verify matured crypto forecasts")
    p.add_argument("--config", default="config_forecast.yaml")
    p.add_argument("--interval", type=int, default=60, help="check interval in seconds")
    p.add_argument("--limit", type=int, default=100, help="max due forecasts to check per cycle")
    p.add_argument("--once", action="store_true", help="run one verification cycle and exit")
    return p.parse_args()


def load_results_dir(config_path: str) -> str:
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return str(cfg.get("results_dir", "results/forecast"))


def run_cycle(results_dir: str, limit: int) -> dict:
    result = settle_due_forecasts(
        results_dir,
        candle_fetcher=fetch_closed_candle,
        limit=limit,
    )
    status = store_status(results_dir)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(
        f"[{now}] checked={result['checked']} settled={result['settled']} "
        f"due={status['due']} pending={status['pending']} total={status['total']}"
    )
    for error in result["errors"]:
        print(f"  WARN {error}")
    return result


def main() -> None:
    args = parse_args()
    results_dir = load_results_dir(args.config)
    interval = max(10, int(args.interval))

    print("Forecast verification monitor")
    print(f"Store: {results_dir}")
    print(f"Interval: {interval}s")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            run_cycle(results_dir, max(1, int(args.limit)))
            if args.once:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")


if __name__ == "__main__":
    main()
