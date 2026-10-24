from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

import pandas as pd

from quant_mm.forecast_store import load_forecast_records, persist_forecasts


_TIMEFRAME_RE = re.compile(r"^(\d+)([mhdw])$")


def timeframe_to_timedelta(timeframe: str) -> pd.Timedelta:
    """Convert common CCXT timeframes such as 15m/1h/4h/1d to Timedelta."""
    match = _TIMEFRAME_RE.match(timeframe.strip().lower())
    if not match:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    value = int(match.group(1))
    unit = match.group(2)
    mapping = {"m": "min", "h": "h", "d": "D", "w": "W"}
    return pd.Timedelta(value, unit=mapping[unit])


def build_price_projection(
    market: pd.DataFrame,
    expected_price: float,
    timeframe: str,
    history_points: int = 240,
) -> pd.DataFrame:
    """Build a compact actual + one-step forecast series for the dashboard chart."""
    if market.empty:
        raise ValueError("market data is empty")
    close = market["close"].astype(float).tail(max(2, int(history_points)))
    next_ts = close.index[-1] + timeframe_to_timedelta(timeframe)
    chart = pd.DataFrame(index=close.index.union(pd.DatetimeIndex([next_ts])))
    chart["实际收盘价"] = close.reindex(chart.index)
    chart["预测收盘价"] = float("nan")
    chart.loc[close.index[-1], "预测收盘价"] = float(close.iloc[-1])
    chart.loc[next_ts, "预测收盘价"] = float(expected_price)
    return chart


def save_forecast_run(rows: Iterable[dict], results_dir: str | Path) -> tuple[Path, Path]:
    """Persist latest forecast, append JSONL history, and insert into SQLite verification store."""
    payload = list(rows)
    out_dir = Path(results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    latest_path = out_dir / "latest_forecast.json"
    with latest_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    history_path = out_dir / "forecast_history.jsonl"
    with history_path.open("a", encoding="utf-8") as f:
        for row in payload:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    persist_forecasts(payload, results_dir)
    return latest_path, history_path


def load_forecast_history(results_dir: str | Path, limit: int = 300) -> pd.DataFrame:
    """Load persistent forecast records, including settlement results when available."""
    try:
        return load_forecast_records(results_dir, limit=limit)
    except Exception:
        # Fallback keeps older installations readable even if the SQLite store
        # cannot be opened for some reason.
        path = Path(results_dir) / "forecast_history.jsonl"
        if not path.exists():
            return pd.DataFrame()

        rows: list[dict] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return pd.DataFrame(rows[-max(1, int(limit)):])
