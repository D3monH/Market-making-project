from __future__ import annotations

from pathlib import Path
import math

import pandas as pd

from .data import (
    ensure_recent_closed_data,
    fetch_forecast_ohlcv,
    forecast_candle_basis,
    load_csv,
    normalize_exchange_id,
    save_cache,
)


def _timeframe_delta(timeframe: str) -> pd.Timedelta:
    value = str(timeframe).strip().lower()
    if not value or value[-1] not in {"m", "h", "d", "w"}:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    number = int(value[:-1])
    unit = {"m": "min", "h": "h", "d": "D", "w": "W"}[value[-1]]
    return pd.Timedelta(number, unit=unit)


def market_cache_path(
    cache_dir: str | Path,
    exchange: str,
    symbol: str,
    timeframe: str,
) -> Path:
    safe_symbol = symbol.replace("/", "_").replace(":", "_")
    resolved = normalize_exchange_id(exchange)
    return Path(cache_dir) / resolved / safe_symbol / f"{timeframe}.csv"


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


def _read_cache(path: Path) -> pd.DataFrame:
    if not path.exists():
        return _empty_frame()
    try:
        frame = load_csv(path)
    except Exception:
        return _empty_frame()
    return frame.sort_index()


def _merge_frames(*frames: pd.DataFrame) -> pd.DataFrame:
    usable = [frame for frame in frames if frame is not None and not frame.empty]
    if not usable:
        return _empty_frame()
    merged = pd.concat(usable).sort_index()
    merged = merged[~merged.index.duplicated(keep="last")]
    merged.index.name = "timestamp"
    return merged


def _missing_intervals(frame: pd.DataFrame, timeframe: str) -> int:
    if len(frame) < 2:
        return 0
    delta = _timeframe_delta(timeframe)
    diffs = pd.DatetimeIndex(frame.index).to_series().diff().dropna()
    missing = 0
    for gap in diffs:
        if gap > delta:
            missing += max(0, int(round(gap / delta)) - 1)
    return int(missing)


def load_cached_forecast_ohlcv(
    exchange: str,
    symbol: str,
    timeframe: str,
    bars: int,
    *,
    cache_dir: str | Path = "data/cache/ohlcv",
    use_cache: bool = True,
    daily_timezone: str = "UTC",
    refresh_floor_bars: int = 12,
) -> pd.DataFrame:
    """Load forecast OHLCV from a persistent local cache and incrementally refresh it.

    First use downloads the requested history window. Later calls estimate how
    many candles could have appeared since the cached last candle and fetch only
    a small recent overlap. The overlap is merged/deduplicated so revisions and
    boundary candles are refreshed safely.

    A recent cache can be used temporarily if the exchange is unreachable. A
    stale cache is still rejected by ``ensure_recent_closed_data`` so an offline
    fallback can never silently generate a formal forecast from old candles.
    """
    if bars <= 0:
        raise ValueError("bars must be greater than zero")

    path = market_cache_path(cache_dir, exchange, symbol, timeframe)
    cached = _read_cache(path) if use_cache else _empty_frame()
    downloaded_rows = 0
    refresh_mode = "network_only"
    refresh_error: str | None = None

    if not use_cache or cached.empty:
        fresh = fetch_forecast_ohlcv(
            exchange,
            symbol,
            timeframe,
            bars,
            daily_timezone=daily_timezone,
        )
        merged = fresh.copy().sort_index()
        merged.index.name = "timestamp"
        downloaded_rows = len(fresh)
        refresh_mode = "cold_start"
    else:
        delta = _timeframe_delta(timeframe)
        now = pd.Timestamp.now(tz="UTC")
        last_open = pd.Timestamp(cached.index[-1])
        if last_open.tzinfo is None:
            last_open = last_open.tz_localize("UTC")
        else:
            last_open = last_open.tz_convert("UTC")

        elapsed = max(pd.Timedelta(0), now - (last_open + delta))
        possible_new = max(0, int(math.floor(elapsed / delta)) + 1)
        refresh_bars = max(int(refresh_floor_bars), possible_new + 6)
        refresh_bars = min(max(refresh_bars, 1), max(int(bars), 1000))

        try:
            fresh = fetch_forecast_ohlcv(
                exchange,
                symbol,
                timeframe,
                refresh_bars,
                daily_timezone=daily_timezone,
            )
            downloaded_rows = len(fresh)
            merged = _merge_frames(cached, fresh)
            refresh_mode = "incremental"

            requested_tail = merged.tail(bars)
            if _missing_intervals(requested_tail, timeframe) > 0:
                repair = fetch_forecast_ohlcv(
                    exchange,
                    symbol,
                    timeframe,
                    bars,
                    daily_timezone=daily_timezone,
                )
                downloaded_rows += len(repair)
                merged = _merge_frames(merged, repair)
                refresh_mode = "gap_repair"
        except Exception as exc:
            merged = cached.copy().sort_index()
            merged.index.name = "timestamp"
            refresh_mode = "offline_cache"
            refresh_error = str(exc)

    result = merged.tail(bars).copy()
    if result.empty:
        raise RuntimeError(f"No cached OHLCV available for {symbol} / {timeframe}")

    ensure_recent_closed_data(result, timeframe)

    if use_cache and refresh_mode != "offline_cache":
        path.parent.mkdir(parents=True, exist_ok=True)
        cache_to_write = merged.copy()
        cache_to_write.index.name = "timestamp"
        save_cache(cache_to_write, path)

    result.attrs["cache_enabled"] = bool(use_cache)
    result.attrs["cache_path"] = str(path)
    result.attrs["cache_rows_total"] = int(len(merged))
    result.attrs["requested_bars"] = int(bars)
    result.attrs["returned_bars"] = int(len(result))
    result.attrs["downloaded_rows_this_run"] = int(downloaded_rows)
    result.attrs["cache_refresh_mode"] = refresh_mode
    result.attrs["cache_refresh_error"] = refresh_error
    result.attrs["missing_intervals"] = _missing_intervals(result, timeframe)
    result.attrs["last_candle_open_at"] = pd.Timestamp(result.index[-1]).isoformat()
    result.attrs["candle_basis"] = forecast_candle_basis(
        exchange,
        timeframe,
        daily_timezone,
    )
    return result
