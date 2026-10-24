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

# Some venues reject OHLCV requests whose `since` timestamp is farther back
# than a fixed number of candles. Gate currently rejects requests older than
# roughly 10,000 interval points. Keep a safety margin so clock/boundary
# differences cannot turn an otherwise valid request into INVALID_PARAM_VALUE.
EXCHANGE_OHLCV_MAX_LOOKBACK_POINTS = {
    "gate": 10_000,
}
OHLCV_LOOKBACK_SAFETY_POINTS = 100


def normalize_exchange_id(exchange_id: str) -> str:
    value = str(exchange_id).strip().lower()
    return EXCHANGE_ALIASES.get(value, value)


def forecast_candle_basis(
    exchange_id: str,
    timeframe: str,
    daily_timezone: str | None = None,
) -> str:
    """Return the persistence cohort for one forecast candle definition.

    New daily forecasts use each venue's native 1d candle. Gate's native daily
    candle is UTC-boundary based; when displayed in Asia/Shanghai its close is
    08:00 Beijing time. `daily_timezone` is accepted only for backward API
    compatibility and does not change new 1d candle boundaries.
    """
    resolved = normalize_exchange_id(exchange_id)
    if str(timeframe).strip().lower() == "1d":
        if resolved == "gate":
            return "gate_native_utc_day"
        return "exchange_native_1d"
    return "exchange_native_closed"


def ohlcv_history_point_limit(exchange_id: str) -> int | None:
    """Return the venue's known maximum OHLCV lookback in interval points."""
    return EXCHANGE_OHLCV_MAX_LOOKBACK_POINTS.get(normalize_exchange_id(exchange_id))


def _initial_since_ms(
    now_ms: int,
    timeframe_ms: int,
    bars: int,
    max_lookback_points: int | None = None,
) -> int:
    """Choose a recent-enough starting timestamp for paginated OHLCV download.

    Normally we start about 10% before the requested window to tolerate gaps.
    When a venue has a hard history boundary, the buffered request is clamped
    inside that boundary.
    """
    requested_points = max(1, int(float(bars) * 1.10))
    if max_lookback_points is not None:
        safe_points = max(1, int(max_lookback_points) - OHLCV_LOOKBACK_SAFETY_POINTS)
        requested_points = min(requested_points, safe_points)
    return now_ms - int(requested_points * timeframe_ms)


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
    """Build legacy natural-day candles for backward-compatible verification.

    This helper is intentionally retained because releases before the native-day
    correction stored `natural_day:Asia/Shanghai` 1d forecasts. New forecasts do
    not call this function; they use the venue's native 1d candle directly.
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
    """Reject silently stale forecast inputs."""
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


def _fetch_legacy_natural_day_candle(
    exchange,
    symbol: str,
    target_ms: int,
    now_ms: int,
) -> dict | None:
    """Grade old Asia/Shanghai-natural-day records created before the fix."""
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


def fetch_closed_candle(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    candle_open_at,
) -> dict | None:
    """Fetch one exact historical forecast candle after it has fully closed.

    New 1d forecasts are exchange-native. Gate native 1d candles are UTC-boundary
    candles, which display as 08:00 -> next-day 08:00 in Asia/Shanghai.

    Old releases created Asia/Shanghai natural-day candles whose UTC open time is
    16:00. For backward compatibility, a legacy 1d target that is not aligned to
    UTC midnight is still reconstructed from six exact 4h candles.
    """
    exchange, resolved_exchange_id = _create_exchange(exchange_id)
    if not exchange.has.get("fetchOHLCV"):
        raise ValueError(f"{resolved_exchange_id} does not support fetchOHLCV through CCXT")

    target_ts = _utc_timestamp(candle_open_at)
    target_ms = int(target_ts.timestamp() * 1000)
    now_ms = int(exchange.milliseconds())

    normalized_tf = timeframe.strip().lower()
    if normalized_tf == "1d" and (
        target_ts.hour != 0 or target_ts.minute != 0 or target_ts.second != 0
    ):
        return _fetch_legacy_natural_day_candle(exchange, symbol, target_ms, now_ms)

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
    """Download the most recent exchange-native closed OHLCV candles.

    If a venue exposes a hard history boundary, requests larger than that
    boundary degrade gracefully to the maximum recent window instead of asking
    for an invalid ancient `since` timestamp.
    """
    if bars <= 0:
        raise ValueError("bars must be greater than zero")

    exchange, resolved_exchange_id = _create_exchange(exchange_id)
    if not exchange.has.get("fetchOHLCV"):
        raise ValueError(f"{resolved_exchange_id} does not support fetchOHLCV through CCXT")

    timeframe_ms = int(exchange.parse_timeframe(timeframe) * 1000)
    now_ms = int(exchange.milliseconds())
    history_limit = ohlcv_history_point_limit(resolved_exchange_id)
    since = _initial_since_ms(
        now_ms,
        timeframe_ms,
        bars,
        max_lookback_points=history_limit,
    )
    rows: list[list] = []

    effective_target_bars = int(bars)
    if history_limit is not None:
        effective_target_bars = min(
            effective_target_bars,
            max(1, int(history_limit) - OHLCV_LOOKBACK_SAFETY_POINTS),
        )

    request_limit = min(1000, max(1, effective_target_bars))
    max_calls = max(20, min(500, effective_target_bars // 25 + 30))
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
    result = df.set_index("timestamp")
    result.attrs["requested_bars"] = int(bars)
    result.attrs["returned_bars"] = int(len(result))
    result.attrs["venue_history_limit_points"] = history_limit
    result.attrs["history_limited"] = bool(
        history_limit is not None and int(bars) > int(len(result))
    )
    return result


def fetch_forecast_ohlcv(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    bars: int,
    daily_timezone: str = DEFAULT_FORECAST_DAY_TIMEZONE,
) -> pd.DataFrame:
    """Return fully closed exchange-native OHLCV for forecasting.

    All new forecast periods, including 1d, preserve the exchange's native
    candle boundaries. For Gate this means a native 1d candle opens at UTC 00:00
    and closes at the next UTC 00:00; when displayed in Asia/Shanghai, that is
    08:00 -> next-day 08:00.

    `daily_timezone` is retained only for call-site compatibility with older
    releases. It no longer changes new 1d candle construction.
    """
    del daily_timezone
    frame = fetch_ohlcv(exchange_id, symbol, timeframe, bars)
    ensure_recent_closed_data(frame, timeframe)
    frame.attrs["candle_basis"] = forecast_candle_basis(exchange_id, timeframe)
    return frame


def cache_path(cache_dir: str | Path, exchange: str, symbol: str, timeframe: str) -> Path:
    safe_symbol = symbol.replace("/", "_").replace(":", "_")
    return Path(cache_dir) / f"{exchange}_{safe_symbol}_{timeframe}.csv"


def save_cache(df: pd.DataFrame, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    df.reset_index().to_csv(target, index=False)