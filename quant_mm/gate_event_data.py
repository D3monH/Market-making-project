from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

GATE_API = "https://api.gateio.ws/api/v4"
EVENT_ASSETS = ("BTC", "ETH", "SOL", "XRP", "DOGE", "HYPE", "BNB")
EVENT_TIMEFRAMES = ("5m", "15m", "1h", "4h")
INDEX_ASSETS = ("BTC", "ETH")
INDEX_TIMEFRAMES = ("5m", "15m")

_TIMEFRAME_SECONDS = {
    "5m": 5 * 60,
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
}


def timeframe_seconds(timeframe: str) -> int:
    try:
        return _TIMEFRAME_SECONDS[str(timeframe)]
    except KeyError as exc:
        raise ValueError(f"Unsupported event timeframe: {timeframe}") from exc


def symbol_to_asset(symbol: str) -> str:
    asset = str(symbol).split("/")[0].upper()
    if asset not in EVENT_ASSETS:
        raise ValueError(f"Unsupported Gate event asset: {asset}")
    return asset


def event_symbol(asset: str) -> str:
    asset = str(asset).upper()
    if asset not in EVENT_ASSETS:
        raise ValueError(f"Unsupported Gate event asset: {asset}")
    return f"{asset}/USDT"


def gate_contract(asset: str) -> str:
    return f"{str(asset).upper()}_USDT"


def gate_index_contract(asset: str) -> str:
    return f"index_{gate_contract(asset)}"


def supports_index_market(asset: str, timeframe: str) -> bool:
    return str(asset).upper() in INDEX_ASSETS and str(timeframe) in INDEX_TIMEFRAMES


def _get_json(
    path: str,
    params: dict[str, Any],
    *,
    timeout: float = 15.0,
    attempts: int = 3,
):
    last_error: Exception | None = None
    for attempt in range(max(1, int(attempts))):
        try:
            response = requests.get(
                GATE_API + path,
                params=params,
                headers={"Accept": "application/json"},
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            time.sleep(0.5 * (attempt + 1))
    if last_error is not None:
        raise last_error
    raise RuntimeError("Gate event market request failed")


def _parse_futures_candles(rows: list[dict], *, include_volume: bool) -> pd.DataFrame:
    parsed: list[dict[str, float | pd.Timestamp]] = []
    for row in rows or []:
        try:
            parsed_row: dict[str, float | pd.Timestamp] = {
                "timestamp": pd.to_datetime(int(float(row["t"])), unit="s", utc=True),
                "open": float(row["o"]),
                "high": float(row["h"]),
                "low": float(row["l"]),
                "close": float(row["c"]),
            }
            if include_volume:
                parsed_row["volume"] = float(row.get("v") or 0.0)
            parsed.append(parsed_row)
        except (KeyError, TypeError, ValueError):
            continue
    if not parsed:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(parsed).drop_duplicates("timestamp", keep="last")
    frame = frame.set_index("timestamp").sort_index()
    if "volume" not in frame:
        frame["volume"] = 0.0
    return frame[["open", "high", "low", "close", "volume"]]


def fetch_gate_futures_candles(
    asset: str,
    timeframe: str,
    *,
    limit: int = 1900,
    index_price: bool = False,
) -> pd.DataFrame:
    """Fetch recent Gate futures/index candles.

    Gate documents a maximum of 2000 points for futures candlesticks. This
    helper intentionally stays below that ceiling and filters the currently
    forming candle so forecasting always starts from a fully closed bar.
    """
    asset = str(asset).upper()
    timeframe = str(timeframe)
    if timeframe not in EVENT_TIMEFRAMES:
        raise ValueError(f"Unsupported event timeframe: {timeframe}")
    contract = gate_index_contract(asset) if index_price else gate_contract(asset)
    rows = _get_json(
        "/futures/usdt/candlesticks",
        {
            "contract": contract,
            "interval": timeframe,
            "limit": min(max(int(limit), 100), 1900),
            "timezone": "utc0",
        },
    )
    frame = _parse_futures_candles(rows if isinstance(rows, list) else [], include_volume=not index_price)
    if frame.empty:
        raise ValueError(f"Gate returned no {'index' if index_price else 'futures'} candles for {asset} {timeframe}")
    delta = pd.Timedelta(seconds=timeframe_seconds(timeframe))
    now = pd.Timestamp.now(tz="UTC")
    frame = frame.loc[(frame.index + delta) <= now]
    if frame.empty:
        raise ValueError("Gate event candle response contains no fully closed bars")
    return frame


def _cache_path(cache_dir: str | Path, asset: str, timeframe: str) -> Path:
    return Path(cache_dir) / "gate" / "index" / asset.upper() / f"{timeframe}.csv"


def _read_cache(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        frame = pd.read_csv(path, parse_dates=["timestamp"]).set_index("timestamp")
        frame.index = pd.to_datetime(frame.index, utc=True)
        return frame.sort_index()
    except Exception:
        return pd.DataFrame()


def _write_cache(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = frame.copy().sort_index()
    out.index.name = "timestamp"
    out.to_csv(path)


def load_gate_index_market(
    asset: str,
    timeframe: str,
    *,
    bars: int = 1800,
    cache_dir: str | Path = "data/cache/event_contracts",
) -> pd.DataFrame:
    """Load Gate INDEX OHLC with regular futures volume aligned as a feature.

    INDEX prices remain the authoritative OHLC source. The companion futures
    volume is used only as an activity feature because Gate index candles do not
    carry traded volume.
    """
    asset = str(asset).upper()
    timeframe = str(timeframe)
    if not supports_index_market(asset, timeframe):
        raise ValueError(
            f"Gate INDEX event research is currently supported for BTC/ETH 5m/15m only: {asset} {timeframe}"
        )

    path = _cache_path(cache_dir, asset, timeframe)
    cached = _read_cache(path)
    request_limit = min(max(int(bars), 900), 1900) if cached.empty else 240

    index_frame = fetch_gate_futures_candles(asset, timeframe, limit=request_limit, index_price=True)
    volume_frame = fetch_gate_futures_candles(asset, timeframe, limit=request_limit, index_price=False)
    index_frame["volume"] = volume_frame["volume"].reindex(index_frame.index).ffill().fillna(0.0)

    if cached.empty:
        merged = index_frame
        refresh_mode = "cold_start"
    else:
        merged = pd.concat([cached, index_frame]).sort_index()
        merged = merged[~merged.index.duplicated(keep="last")]
        refresh_mode = "incremental"

    _write_cache(path, merged)
    result = merged.tail(max(1, int(bars))).copy()
    result.attrs.update(
        {
            "event_benchmark": "INDEX",
            "price_source": "gate_futures_index",
            "cache_refresh_mode": refresh_mode,
            "cache_rows_total": int(len(merged)),
            "downloaded_rows_this_run": int(len(index_frame)),
            "cache_path": str(path),
        }
    )
    return result


def fetch_gate_index_candle(asset: str, timeframe: str, event_open_at) -> dict[str, float] | None:
    """Fetch one exact Gate index candle for INDEX-event settlement."""
    asset = str(asset).upper()
    if not supports_index_market(asset, timeframe):
        return None
    open_ts = pd.Timestamp(event_open_at)
    if open_ts.tzinfo is None:
        open_ts = open_ts.tz_localize("UTC")
    else:
        open_ts = open_ts.tz_convert("UTC")
    seconds = timeframe_seconds(timeframe)
    start = int(open_ts.timestamp())
    rows = _get_json(
        "/futures/usdt/candlesticks",
        {
            "contract": gate_index_contract(asset),
            "interval": timeframe,
            "from": start,
            "to": start + seconds,
            "timezone": "utc0",
        },
    )
    frame = _parse_futures_candles(rows if isinstance(rows, list) else [], include_volume=False)
    if frame.empty:
        return None
    match = frame.loc[frame.index == open_ts]
    if match.empty:
        return None
    row = match.iloc[-1]
    return {
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
    }
