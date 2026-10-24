from __future__ import annotations

from pathlib import Path
import time

import pandas as pd

COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def load_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = set(COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing columns: {sorted(missing)}")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df[COLUMNS].drop_duplicates("timestamp").sort_values("timestamp").set_index("timestamp")


def fetch_ohlcv(exchange_id: str, symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
    """Download recent closed OHLCV candles through CCXT public market-data APIs."""
    try:
        import ccxt
    except ImportError as exc:
        raise RuntimeError(
            "ccxt is required for market-data download: pip install -r requirements.txt"
        ) from exc

    exchange_cls = getattr(ccxt, exchange_id, None)
    if exchange_cls is None:
        raise ValueError(f"Unknown CCXT exchange: {exchange_id}")
    exchange = exchange_cls({"enableRateLimit": True})
    exchange.load_markets()
    if not exchange.has.get("fetchOHLCV"):
        raise ValueError(f"{exchange_id} does not support fetchOHLCV through CCXT")

    timeframe_ms = int(exchange.parse_timeframe(timeframe) * 1000)
    since = exchange.milliseconds() - int(bars * timeframe_ms * 1.10)
    rows: list[list] = []

    while len(rows) < bars:
        limit = min(1000, bars - len(rows))
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
        if not batch:
            break
        rows.extend(batch)
        next_since = int(batch[-1][0]) + timeframe_ms
        if next_since <= since:
            break
        since = next_since
        if len(batch) < limit:
            break
        time.sleep(exchange.rateLimit / 1000.0)

    if not rows:
        raise RuntimeError(f"No OHLCV returned for {symbol} on {exchange_id}")

    df = pd.DataFrame(rows, columns=COLUMNS)
    df = df.drop_duplicates("timestamp").sort_values("timestamp")
    now_ms = exchange.milliseconds()
    df = df[df["timestamp"] + timeframe_ms <= now_ms]
    df = df.tail(bars)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("timestamp")


def cache_path(cache_dir: str | Path, exchange: str, symbol: str, timeframe: str) -> Path:
    safe_symbol = symbol.replace("/", "_").replace(":", "_")
    return Path(cache_dir) / f"{exchange}_{safe_symbol}_{timeframe}.csv"


def save_cache(df: pd.DataFrame, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    df.reset_index().to_csv(target, index=False)
