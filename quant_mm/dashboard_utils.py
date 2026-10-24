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


def forecast_window(as_of, timeframe: str) -> dict[str, pd.Timestamp]:
    """Return the base and target candle boundaries for a one-candle forecast.

    CCXT OHLCV timestamps are candle OPEN times. For example, if a 15m
    forecast has `as_of=20:00`, the model's base price is the close at 20:15,
    and the target is the next candle close at 20:30.
    """
    base_open = pd.Timestamp(as_of)
    if base_open.tzinfo is None:
        base_open = base_open.tz_localize("UTC")
    delta = timeframe_to_timedelta(timeframe)
    base_close = base_open + delta
    target_open = base_close
    target_close = target_open + delta
    return {
        "base_open_at": base_open,
        "base_close_at": base_close,
        "target_open_at": target_open,
        "target_close_at": target_close,
    }


def build_price_projection(
    market: pd.DataFrame,
    expected_price: float,
    timeframe: str,
    history_points: int = 240,
) -> pd.DataFrame:
    """Build a close-time chart plus the next candle close forecast.

    Market frame indexes are candle open timestamps, so shift historical close
    values to their actual close timestamps before drawing the chart.
    """
    if market.empty:
        raise ValueError("market data is empty")

    delta = timeframe_to_timedelta(timeframe)
    close = market["close"].astype(float).tail(max(2, int(history_points))).copy()
    close.index = close.index + delta
    next_close_ts = close.index[-1] + delta

    chart = pd.DataFrame(index=close.index.union(pd.DatetimeIndex([next_close_ts])))
    chart["实际收盘价"] = close.reindex(chart.index)
    chart["预测收盘价"] = float("nan")
    chart.loc[close.index[-1], "预测收盘价"] = float(close.iloc[-1])
    chart.loc[next_close_ts, "预测收盘价"] = float(expected_price)
    return chart


def save_forecast_run(rows: Iterable[dict], results_dir: str | Path) -> tuple[Path, Path]:
    """Persist the latest run and store each real target candle only once.

    SQLite is the authoritative history for complete forecasts. Legacy JSONL is
    retained only for lightweight/incomplete records used by older tools, so
    repeated clicks cannot keep appending duplicate real forecasts.
    """
    payload = list(rows)
    out_dir = Path(results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    latest_path = out_dir / "latest_forecast.json"
    with latest_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    history_path = out_dir / "forecast_history.jsonl"
    history_path.touch(exist_ok=True)

    required = {
        "exchange",
        "symbol",
        "timeframe",
        "forecast_run_at",
        "as_of",
        "current_price",
        "expected_return_pct",
        "expected_price",
    }
    verifiable = [row for row in payload if required.issubset(row)]
    lightweight = [row for row in payload if not required.issubset(row)]

    if verifiable:
        persist_forecasts(verifiable, results_dir)

    if lightweight:
        with history_path.open("a", encoding="utf-8") as f:
            for row in lightweight:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    return latest_path, history_path


def load_forecast_history(results_dir: str | Path, limit: int = 300) -> pd.DataFrame:
    """Load persistent forecast records, including settlement results when available."""
    try:
        records = load_forecast_records(results_dir, limit=limit)
        if not records.empty:
            return records
    except Exception:
        pass

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
