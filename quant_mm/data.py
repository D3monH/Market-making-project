from __future__ import annotations

from pathlib import Path
import time

import pandas as pd

COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

EXCHANGE_ALIASES = {
    # CCXT renamed/standardized Gate's exchange id to `gate`.
    # Keep the legacy `gateio` value working for old config files and UI state.
    "gateio": "gate",
}


def normalize_exchange_id(exchange_id: str) -> str:
    value = str(exchange_id).strip().lower()
    return EXCHANGE_ALIASES.get(value, value)


def _initial_since_ms(now_ms: int, timeframe_ms: int, bars: int) -> int:
    """Start slightly before the requested window so gaps can be tolerated.

    The caller must still paginate all the way to `now_ms`; the buffer is not a
    reason to stop once `bars` rows have been collected.
    """
    return int(now_ms - bars * timeframe_ms * 1.10)


def _create_exchange(exchange_id: str):
    try:
        import ccxt
    except ImportError as exc:
        raise RuntimeError(
            "ccxt is required for market-data download: pip install -r requirements.txt"
        ) from exc

    requested_exchange_id = exchange_id
    resolved_exchange_id = normalize_exchange_id(exchange_id)
    exchange_cls = getattr(ccxt, resolved_exchange_id, None)
    if exchange_cls is None:
        raise ValueError(
            f"Unknown CCXT exchange: {requested_exchange_id} (resolved as {resolved_exchange_id})"
        )

    exchange = exchange_cls({"enableRateLimit": True})
    exchange.load_markets()
    return exchange, resolved_exchange_id


def load_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = set(COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing columns: {sorted(missing)}")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df[COLUMNS].drop_duplicates("timestamp").sort_values("timestamp").set_index("timestamp")


def fetch_last_price(exchange_id: str, symbol: str) -> float:
    """Return the exchange's latest public ticker price for display purposes."""
    exchange, resolved_exchange_id = _create_exchange(exchange_id)
    if not exchange.has.get("fetchTicker"):
        raise ValueError(f"{resolved_exchange_id} does not support fetchTicker through CCXT")

    ticker = exchange.fetch_ticker(symbol)
    price = ticker.get("last")
    if price is None:
        price = ticker.get("close")
    if price is None:
        bid = ticker.get("bid")
        ask = ticker.get("ask")
        if bid is not None and ask is not None:
            price = (float(bid) + float(ask)) / 2.0
    if price is None:
        raise RuntimeError(f"No latest ticker price returned for {symbol} on {resolved_exchange_id}")
    return float(price)


def fetch_ohlcv(exchange_id: str, symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
    """Download the most recent *closed* OHLCV candles through CCXT.

    Important: the function paginates until it reaches the latest market data,
    then keeps only the final `bars` closed candles. It must not stop merely
    because `bars` rows were collected from an older buffered start point.
    """
    if bars <= 0:
        raise ValueError("bars must be greater than zero")

    exchange, resolved_exchange_id = _create_exchange(exchange_id)
    if not exchange.has.get("fetchOHLCV"):
        raise ValueError(f"{resolved_exchange_id} does not support fetchOHLCV through CCXT")

    timeframe_ms = int(exchange.parse_timeframe(timeframe) * 1000)
    now_ms = int(exchange.milliseconds())
    since = _initial_since_ms(now_ms, timeframe_ms, bars)
    rows: list[list] = []

    # Request a reasonably large page, but do not assume every exchange will
    # actually return that many rows. Some venues silently cap page sizes.
    request_limit = min(1000, max(1, bars))
    max_calls = max(20, min(500, bars // 25 + 30))
    calls = 0

    while since < now_ms and calls < max_calls:
        calls += 1
        batch = exchange.fetch_ohlcv(
            symbol,
            timeframe=timeframe,
            since=since,
            limit=request_limit,
        )
        if not batch:
            break

        rows.extend(batch)
        last_timestamp = int(batch[-1][0])
        next_since = last_timestamp + timeframe_ms
        if next_since <= since:
            break
        since = next_since

        # If the exchange returned the currently-forming candle, we have
        # reached real time. It will be removed by the closed-candle filter.
        if last_timestamp + timeframe_ms > now_ms:
            break

        # Do NOT break just because len(batch) < request_limit. Several
        # exchanges cap their response below the requested limit.
        time.sleep(exchange.rateLimit / 1000.0)

    if not rows:
        raise RuntimeError(f"No OHLCV returned for {symbol} on {resolved_exchange_id}")

    df = pd.DataFrame(rows, columns=COLUMNS)
    df = df.drop_duplicates("timestamp").sort_values("timestamp")

    # Re-read exchange time after pagination in case the download itself took a
    # while. Only completed candles are allowed into model features.
    final_now_ms = int(exchange.milliseconds())
    df = df[df["timestamp"] + timeframe_ms <= final_now_ms]
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
