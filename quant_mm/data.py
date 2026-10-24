from __future__ import annotations

from pathlib import Path
import time

import pandas as pd

COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
DEFAULT_FORECAST_DAY_TIMEZONE = "Asia/Shanghai"
FORECAST_DAILY_BASE_TIMEFRAME = "4h"

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
    return now_ms - int(bars * timeframe_ms * 1.10)


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


def _utc_timestamp(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts


def _timeframe_delta(timeframe: str) -> pd.Timedelta:
    value = str(timeframe).strip().lower()
    if not value or value[-1] not in {"m", "h", "d", "w"}:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    number = int(value[:-1])
    unit = {"m": "min", "h": "h", "d": "D", "w": "W"}[value[-1]]
    return pd.Timedelta(number, unit=unit)


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


def resample_daily_ohlcv(
    base: pd.DataFrame,
    timezone_name: str = DEFAULT_FORECAST_DAY_TIMEZONE,
    base_timeframe: str = FORECAST_DAILY_BASE_TIMEFRAME,
    now=None,
) -> pd.DataFrame:
    """Build closed natural-day crypto candles in a chosen timezone.

    For the default Asia/Shanghai timezone, each output candle represents
    Beijing 00:00 -> next-day 00:00. The returned index remains the candle OPEN
    timestamp in UTC so the rest of the forecast pipeline keeps one consistent
    timestamp convention.

    The function requires a complete set of base candles for the day. With the
    default 4h base timeframe that means exactly six 4h candles per day.
    """
    if base.empty:
        return base.copy()

    frame = base.copy().sort_index()
    index = pd.DatetimeIndex(frame.index)
    if index.tz is None:
        index = index.tz_localize("UTC")
    else:
        index = index.tz_convert("UTC")
    frame.index = index.tz_convert(timezone_name)

    base_delta = _timeframe_delta(base_timeframe)
    one_day = pd.Timedelta(days=1)
    expected_parts_float = one_day / base_delta
    expected_parts = int(expected_parts_float)
    if expected_parts <= 0 or base_delta * expected_parts != one_day:
        raise ValueError(f"Base timeframe {base_timeframe} must divide one day exactly")

    grouped = frame.resample("1D", label="left", closed="left")
    daily = grouped.agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    counts = grouped["close"].count()

    now_ts = pd.Timestamp.now(tz="UTC") if now is None else _utc_timestamp(now)
    now_local = now_ts.tz_convert(timezone_name)
    complete_mask = (counts >= expected_parts) & ((daily.index + one_day) <= now_local)
    daily = daily.loc[complete_mask].dropna(subset=["open", "high", "low", "close"])
    daily.index = daily.index.tz_convert("UTC")
    return daily


def latest_candle_close_at(df: pd.DataFrame, timeframe: str) -> pd.Timestamp:
    if df.empty:
        raise ValueError("market data is empty")
    last_open = _utc_timestamp(df.index[-1])
    return last_open + _timeframe_delta(timeframe)


def ensure_recent_closed_data(
    df: pd.DataFrame,
    timeframe: str,
    now=None,
    tolerance_ratio: float = 0.05,
) -> None:
    """Reject silently stale forecast inputs.

    A valid latest closed candle may be almost one full timeframe old while the
    next candle is still forming. Anything older than one timeframe plus a small
    tolerance indicates pagination/alignment/data-source trouble and should not
    be used for a new forecast.
    """
    close_at = latest_candle_close_at(df, timeframe)
    now_ts = pd.Timestamp.now(tz="UTC") if now is None else _utc_timestamp(now)
    delta = _timeframe_delta(timeframe)
    tolerance = max(pd.Timedelta(minutes=2), delta * float(tolerance_ratio))

    if close_at > now_ts + pd.Timedelta(minutes=2):
        raise RuntimeError(
            f"Latest candle closes in the future: close={close_at.isoformat()} now={now_ts.isoformat()}"
        )
    if now_ts - close_at > delta + tolerance:
        raise RuntimeError(
            "Latest closed candle is stale: "
            f"timeframe={timeframe} close={close_at.isoformat()} now={now_ts.isoformat()}"
        )


def _aggregate_rows(rows: list[list], target_ms: int) -> dict:
    if not rows:
        raise ValueError("cannot aggregate empty OHLCV rows")
    return {
        "timestamp": pd.to_datetime(target_ms, unit="ms", utc=True).isoformat(),
        "open": float(rows[0][1]),
        "high": float(max(float(row[2]) for row in rows)),
        "low": float(min(float(row[3]) for row in rows)),
        "close": float(rows[-1][4]),
        "volume": float(sum(float(row[5]) for row in rows)),
    }


def fetch_closed_candle(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    candle_open_at,
) -> dict | None:
    """Fetch one exact historical forecast candle after it has fully closed.

    `candle_open_at` is the UTC open timestamp of the candle being verified.
    For 1d forecasts we intentionally aggregate six exact 4h candles over the
    stored 24-hour target window. This makes verification consistent with
    Beijing-aligned (or any other exact 24-hour aligned) forecast days instead
    of accidentally grading against the exchange's native UTC daily candle.
    """
    exchange, resolved_exchange_id = _create_exchange(exchange_id)
    if not exchange.has.get("fetchOHLCV"):
        raise ValueError(f"{resolved_exchange_id} does not support fetchOHLCV through CCXT")

    target_ts = _utc_timestamp(candle_open_at)
    target_ms = int(target_ts.timestamp() * 1000)
    now_ms = int(exchange.milliseconds())

    if timeframe.strip().lower() == "1d":
        target_delta = pd.Timedelta(days=1)
        target_close_ms = target_ms + int(target_delta.total_seconds() * 1000)
        if target_close_ms > now_ms:
            return None

        base_timeframe = FORECAST_DAILY_BASE_TIMEFRAME
        base_ms = int(exchange.parse_timeframe(base_timeframe) * 1000)
        expected_parts = int(target_delta.total_seconds() * 1000) // base_ms
        batch = exchange.fetch_ohlcv(
            symbol,
            timeframe=base_timeframe,
            since=target_ms,
            limit=expected_parts + 2,
        )
        expected_timestamps = {target_ms + i * base_ms for i in range(expected_parts)}
        rows = [row for row in batch if int(row[0]) in expected_timestamps]
        rows.sort(key=lambda row: int(row[0]))
        if len(rows) != expected_parts:
            return None
        if any(int(row[0]) + base_ms > int(exchange.milliseconds()) for row in rows):
            return None
        return _aggregate_rows(rows, target_ms)

    timeframe_ms = int(exchange.parse_timeframe(timeframe) * 1000)
    if target_ms + timeframe_ms > now_ms:
        return None

    batch = exchange.fetch_ohlcv(
        symbol,
        timeframe=timeframe,
        since=target_ms,
        limit=5,
    )
    if not batch:
        return None

    row = next((item for item in batch if int(item[0]) == target_ms), None)
    if row is None:
        return None
    if int(row[0]) + timeframe_ms > int(exchange.milliseconds()):
        return None

    return {
        "timestamp": pd.to_datetime(int(row[0]), unit="ms", utc=True).isoformat(),
        "open": float(row[1]),
        "high": float(row[2]),
        "low": float(row[3]),
        "close": float(row[4]),
        "volume": float(row[5]),
    }


def fetch_ohlcv(exchange_id: str, symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
    """Download the most recent *exchange-native closed* OHLCV candles.

    This low-level function preserves the venue's native candle boundaries. Use
    `fetch_forecast_ohlcv` for forecasting, because forecast 1d candles have an
    explicit natural-day timezone policy.
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

        if last_timestamp + timeframe_ms > now_ms:
            break

        time.sleep(exchange.rateLimit / 1000.0)

    if not rows:
        raise RuntimeError(f"No OHLCV returned for {symbol} on {resolved_exchange_id}")

    df = pd.DataFrame(rows, columns=COLUMNS)
    df = df.drop_duplicates("timestamp").sort_values("timestamp")

    final_now_ms = int(exchange.milliseconds())
    df = df[df["timestamp"] + timeframe_ms <= final_now_ms]
    df = df.tail(bars)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("timestamp")


def fetch_forecast_ohlcv(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    bars: int,
    daily_timezone: str = DEFAULT_FORECAST_DAY_TIMEZONE,
) -> pd.DataFrame:
    """Return closed OHLCV with deterministic boundaries for forecasting.

    Intraday timeframes use the exchange's native closed candles. A 1d forecast
    is rebuilt from closed 4h candles so that the day is the natural day in
    `daily_timezone` (Asia/Shanghai by default) rather than the exchange's UTC
    day. The final dataset is rejected if its latest closed candle is stale.
    """
    normalized_tf = timeframe.strip().lower()
    if normalized_tf != "1d":
        frame = fetch_ohlcv(exchange_id, symbol, timeframe, bars)
        ensure_recent_closed_data(frame, timeframe)
        return frame

    base_delta = _timeframe_delta(FORECAST_DAILY_BASE_TIMEFRAME)
    parts_per_day = int(pd.Timedelta(days=1) / base_delta)
    raw_bars = max(parts_per_day * int(bars) + parts_per_day * 3, 1000)
    base = fetch_ohlcv(
        exchange_id,
        symbol,
        FORECAST_DAILY_BASE_TIMEFRAME,
        raw_bars,
    )
    daily = resample_daily_ohlcv(
        base,
        timezone_name=daily_timezone,
        base_timeframe=FORECAST_DAILY_BASE_TIMEFRAME,
    ).tail(bars)
    if daily.empty:
        raise RuntimeError(
            f"No complete {daily_timezone} daily candles could be built for {symbol}"
        )
    ensure_recent_closed_data(daily, "1d")
    return daily


def cache_path(cache_dir: str | Path, exchange: str, symbol: str, timeframe: str) -> Path:
    safe_symbol = symbol.replace("/", "_").replace(":", "_")
    return Path(cache_dir) / f"{exchange}_{safe_symbol}_{timeframe}.csv"


def save_cache(df: pd.DataFrame, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    df.reset_index().to_csv(target, index=False)
