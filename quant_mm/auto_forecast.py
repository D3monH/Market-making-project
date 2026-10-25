from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from quant_mm.dashboard_utils import save_forecast_run
from quant_mm.data import fetch_closed_candle, forecast_candle_basis
from quant_mm.forecast_engine import ForecastConfig, forecast_next_candle
from quant_mm.forecast_store import load_forecast_records, settle_due_forecasts
from quant_mm.market_cache import load_cached_forecast_ohlcv
from quant_mm.model_performance import realized_model_weights

SETTINGS_FILENAME = "auto_forecast.json"
STATUS_FILENAME = "auto_forecast_status.json"

DEFAULT_SETTINGS = {
    "enabled": False,
    "exchange": "gate",
    "symbols": ["BTC/USDT"],
    "timeframes": ["15m"],
    "bars": 5000,
    "use_cache": True,
    "poll_seconds": 60,
}


def _settings_path(results_dir: str | Path) -> Path:
    path = Path(results_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path / SETTINGS_FILENAME


def _status_path(results_dir: str | Path) -> Path:
    path = Path(results_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path / STATUS_FILENAME


def normalize_auto_settings(raw: dict | None) -> dict:
    source = dict(DEFAULT_SETTINGS)
    source.update(raw or {})

    exchange = str(source.get("exchange") or "gate").strip().lower()
    if exchange == "gateio":
        exchange = "gate"

    symbols = [str(v).strip().upper() for v in source.get("symbols", []) if str(v).strip()]
    timeframes = [str(v).strip().lower() for v in source.get("timeframes", []) if str(v).strip()]

    return {
        "enabled": bool(source.get("enabled", False)),
        "exchange": exchange,
        "symbols": list(dict.fromkeys(symbols)) or ["BTC/USDT"],
        "timeframes": list(dict.fromkeys(timeframes)) or ["15m"],
        "bars": min(10000, max(1000, int(source.get("bars", 5000)))),
        "use_cache": bool(source.get("use_cache", True)),
        "poll_seconds": min(3600, max(30, int(source.get("poll_seconds", 60)))),
    }


def load_auto_settings(results_dir: str | Path) -> dict:
    path = _settings_path(results_dir)
    if not path.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        return normalize_auto_settings(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return dict(DEFAULT_SETTINGS)


def save_auto_settings(results_dir: str | Path, settings: dict) -> Path:
    normalized = normalize_auto_settings(settings)
    path = _settings_path(results_dir)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_auto_status(results_dir: str | Path) -> dict:
    path = _status_path(results_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_auto_status(results_dir: str | Path, status: dict) -> Path:
    path = _status_path(results_dir)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def _utc_iso(value) -> str:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.isoformat()


def find_existing_forecast(
    results_dir: str | Path,
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    as_of,
) -> dict | None:
    records = load_forecast_records(results_dir, limit=200, symbol=symbol)
    if records.empty:
        return None

    target_as_of = _utc_iso(as_of)
    for record in records.to_dict("records"):
        if str(record.get("exchange", "")) != str(exchange):
            continue
        if str(record.get("timeframe", "")) != str(timeframe):
            continue
        try:
            if _utc_iso(record.get("as_of")) != target_as_of:
                continue
        except Exception:
            continue

        raw = record.get("raw_json")
        if isinstance(raw, str) and raw:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = dict(record)
        else:
            payload = dict(record)
        payload["status"] = record.get("status")
        payload["forecast_id"] = record.get("forecast_id")
        payload["target_close_at"] = record.get("target_close_at")
        payload["actual_close"] = record.get("actual_close")
        payload["actual_return_pct"] = record.get("actual_return_pct")
        payload["direction_correct"] = record.get("direction_correct")
        return payload
    return None


def run_auto_forecast_cycle(
    settings: dict,
    project_config: dict,
    *,
    results_dir: str | Path,
    market_loader: Callable = load_cached_forecast_ohlcv,
    existing_loader: Callable = find_existing_forecast,
    forecast_func: Callable = forecast_next_candle,
    weight_loader: Callable = realized_model_weights,
    saver: Callable = save_forecast_run,
    settle: bool = True,
) -> dict:
    settings = normalize_auto_settings(settings)
    started_at = datetime.now(timezone.utc).isoformat()
    result = {
        "started_at": started_at,
        "enabled": settings["enabled"],
        "generated": 0,
        "skipped_existing": 0,
        "settled": 0,
        "errors": [],
    }
    if not settings["enabled"]:
        return result

    forecast_raw = project_config.get("forecast", {}) or {}
    forecast_cfg = ForecastConfig(**forecast_raw)
    time_cfg = project_config.get("time", {}) or {}
    daily_timezone = str(time_cfg.get("daily_candle_timezone", "UTC"))
    market_cfg = project_config.get("market_data", {}) or {}
    cache_dir = str(market_cfg.get("cache_dir", "data/cache/ohlcv"))

    for symbol in settings["symbols"]:
        for timeframe in settings["timeframes"]:
            try:
                market = market_loader(
                    settings["exchange"],
                    symbol,
                    timeframe,
                    settings["bars"],
                    daily_timezone=daily_timezone,
                    use_cache=settings["use_cache"],
                    cache_dir=cache_dir,
                )
                if market is None or market.empty:
                    raise ValueError("没有可用的已收盘 K 线")

                as_of = market.index[-1]
                existing = existing_loader(
                    results_dir,
                    exchange=settings["exchange"],
                    symbol=symbol,
                    timeframe=timeframe,
                    as_of=as_of,
                )
                if existing is not None:
                    result["skipped_existing"] += 1
                    continue

                candle_basis = forecast_candle_basis(
                    settings["exchange"],
                    timeframe,
                    daily_timezone,
                )
                live_weights = weight_loader(
                    results_dir,
                    symbol=symbol,
                    timeframe=timeframe,
                    candle_basis=candle_basis,
                    min_samples=forecast_cfg.realized_weight_min_samples,
                )
                forecast = forecast_func(
                    market,
                    timeframe=timeframe,
                    cfg=forecast_cfg,
                    realized_model_weights=live_weights,
                )
                row = forecast.to_dict()
                row.update(
                    {
                        "symbol": symbol,
                        "exchange": settings["exchange"],
                        "forecast_run_at": datetime.now(timezone.utc).isoformat(),
                        "candle_basis": candle_basis,
                        "realized_model_weights": live_weights,
                        "forecast_source": "auto",
                    }
                )
                saver([row], results_dir)
                result["generated"] += 1
            except Exception as exc:
                result["errors"].append(f"{symbol} {timeframe}: {exc}")

    if settle:
        try:
            verify = settle_due_forecasts(
                results_dir,
                candle_fetcher=fetch_closed_candle,
                limit=200,
            )
            result["settled"] = int(verify.get("settled", 0))
            result["errors"].extend(verify.get("errors", []))
        except Exception as exc:
            result["errors"].append(f"自动验真: {exc}")

    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    return result
