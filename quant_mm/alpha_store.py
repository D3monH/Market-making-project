from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

DB_FILENAME = "alpha_history.sqlite3"


def _utc(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _delta(timeframe: str) -> pd.Timedelta:
    tf = timeframe.strip().lower()
    n = int(tf[:-1])
    unit = {"m": "min", "h": "h", "d": "D", "w": "W"}[tf[-1]]
    return pd.Timedelta(n, unit=unit)


def _db_path(results_dir: str | Path) -> Path:
    path = Path(results_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path / DB_FILENAME


def _connect(results_dir: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(results_dir))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS alpha_forecasts (
            alpha_id TEXT PRIMARY KEY,
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            as_of TEXT NOT NULL,
            forecast_run_at TEXT NOT NULL,
            target_open_at TEXT NOT NULL,
            target_close_at TEXT NOT NULL,
            base_price REAL NOT NULL,
            model_expected_return_pct REAL NOT NULL,
            model_confidence REAL NOT NULL,
            composite_score REAL NOT NULL,
            consensus_score REAL NOT NULL,
            consensus_signal TEXT NOT NULL,
            consensus_confidence REAL NOT NULL,
            risk_multiplier REAL NOT NULL,
            data_quality REAL NOT NULL,
            factor_scores_json TEXT NOT NULL,
            factor_weights_json TEXT NOT NULL,
            raw_context_json TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            settled_at TEXT,
            actual_close REAL,
            actual_return_pct REAL,
            direction_correct INTEGER,
            signal_correct INTEGER
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_alpha_target
            ON alpha_forecasts(exchange, symbol, timeframe, as_of);
        CREATE INDEX IF NOT EXISTS idx_alpha_due
            ON alpha_forecasts(status, target_close_at);
        """
    )
    return conn


def _id(exchange: str, symbol: str, timeframe: str, as_of: str) -> str:
    key = f"{exchange}|{symbol}|{timeframe}|{as_of}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def persist_alpha(
    results_dir: str | Path,
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    as_of,
    base_price: float,
    model_expected_return_pct: float,
    model_confidence: float,
    alpha: dict,
    forecast_run_at: str | None = None,
) -> int:
    base_open = _utc(as_of)
    delta = _delta(timeframe)
    target_open = base_open + delta
    target_close = target_open + delta
    alpha_id = _id(exchange, symbol, timeframe, base_open.isoformat())

    with _connect(results_dir) as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO alpha_forecasts (
                alpha_id, exchange, symbol, timeframe, as_of, forecast_run_at,
                target_open_at, target_close_at, base_price,
                model_expected_return_pct, model_confidence,
                composite_score, consensus_score, consensus_signal,
                consensus_confidence, risk_multiplier, data_quality,
                factor_scores_json, factor_weights_json, raw_context_json, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alpha_id,
                exchange,
                symbol,
                timeframe,
                base_open.isoformat(),
                forecast_run_at or datetime.now(timezone.utc).isoformat(),
                target_open.isoformat(),
                target_close.isoformat(),
                float(base_price),
                float(model_expected_return_pct),
                float(model_confidence),
                float(alpha["composite_score"]),
                float(alpha["consensus_score"]),
                str(alpha["consensus_signal"]),
                float(alpha["consensus_confidence"]),
                float(alpha["risk_multiplier"]),
                float(alpha["data_quality"]),
                json.dumps(alpha.get("factor_scores", {}), ensure_ascii=False),
                json.dumps(alpha.get("factor_weights", {}), ensure_ascii=False),
                json.dumps(alpha.get("raw_context", {}), ensure_ascii=False),
                str(alpha.get("source", "")),
            ),
        )
        return int(cur.rowcount > 0)


def _signal_correct(signal: str, actual: float) -> int | None:
    if signal in {"LONG_BIAS", "STRONG_LONG_BIAS"}:
        return int(actual > 0)
    if signal in {"SHORT_BIAS", "STRONG_SHORT_BIAS"}:
        return int(actual < 0)
    return None


def settle_due_alpha(
    results_dir: str | Path,
    candle_fetcher: Callable[[str, str, str, str], dict | None],
    *,
    now=None,
    limit: int = 100,
) -> dict:
    now_ts = _utc(now or datetime.now(timezone.utc)).isoformat()
    with _connect(results_dir) as conn:
        due = conn.execute(
            """
            SELECT * FROM alpha_forecasts
            WHERE status='PENDING' AND target_close_at <= ?
            ORDER BY target_close_at ASC LIMIT ?
            """,
            (now_ts, max(1, int(limit))),
        ).fetchall()

        settled = 0
        errors: list[str] = []
        for record in due:
            row = dict(record)
            try:
                candle = candle_fetcher(
                    row["exchange"], row["symbol"], row["timeframe"], row["target_open_at"]
                )
                if candle is None:
                    continue
                actual_close = float(candle["close"])
                actual_return = (actual_close / float(row["base_price"]) - 1.0) * 100.0
                score = float(row["consensus_score"])
                direction_correct = int((score > 0 and actual_return > 0) or (score < 0 and actual_return < 0))
                signal_correct = _signal_correct(str(row["consensus_signal"]), actual_return)
                conn.execute(
                    """
                    UPDATE alpha_forecasts SET status='SETTLED', settled_at=?,
                        actual_close=?, actual_return_pct=?, direction_correct=?, signal_correct=?
                    WHERE alpha_id=? AND status='PENDING'
                    """,
                    (
                        datetime.now(timezone.utc).isoformat(),
                        actual_close,
                        actual_return,
                        direction_correct,
                        signal_correct,
                        row["alpha_id"],
                    ),
                )
                settled += 1
            except Exception as exc:
                errors.append(f"{row['symbol']} {row['timeframe']}: {exc}")

        remaining = conn.execute(
            "SELECT COUNT(*) FROM alpha_forecasts WHERE status='PENDING' AND target_close_at <= ?",
            (now_ts,),
        ).fetchone()[0]
    return {"checked": len(due), "settled": settled, "remaining_due": int(remaining), "errors": errors}


def alpha_summary(results_dir: str | Path, symbol: str | None = None) -> pd.DataFrame:
    where = "WHERE status='SETTLED'"
    params: list[object] = []
    if symbol:
        where += " AND symbol=?"
        params.append(symbol)
    sql = f"""
        SELECT symbol, timeframe, COUNT(*) AS samples,
               AVG(direction_correct) AS direction_accuracy,
               AVG(CASE WHEN signal_correct IS NOT NULL THEN signal_correct END) AS signal_accuracy,
               AVG(ABS(actual_return_pct)) AS avg_abs_move_pct,
               AVG(consensus_confidence) AS avg_confidence,
               AVG(data_quality) AS avg_data_quality
        FROM alpha_forecasts
        {where}
        GROUP BY symbol, timeframe
        ORDER BY symbol, timeframe
    """
    with _connect(results_dir) as conn:
        rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame([dict(row) for row in rows])


def factor_leaderboard(
    results_dir: str | Path,
    *,
    symbol: str,
    timeframe: str,
    limit: int = 500,
) -> pd.DataFrame:
    with _connect(results_dir) as conn:
        rows = conn.execute(
            """
            SELECT factor_scores_json, actual_return_pct
            FROM alpha_forecasts
            WHERE status='SETTLED' AND symbol=? AND timeframe=?
            ORDER BY target_close_at DESC LIMIT ?
            """,
            (symbol, timeframe, max(1, int(limit))),
        ).fetchall()

    stats: dict[str, dict[str, float]] = {}
    for record in rows:
        actual = float(record["actual_return_pct"])
        try:
            factors = json.loads(record["factor_scores_json"] or "{}")
        except json.JSONDecodeError:
            factors = {}
        for name, value in factors.items():
            score = float(value)
            if abs(score) < 1e-12 or abs(actual) < 1e-12:
                continue
            bucket = stats.setdefault(name, {"samples": 0.0, "correct": 0.0, "edge": 0.0})
            bucket["samples"] += 1.0
            bucket["correct"] += float((score > 0 and actual > 0) or (score < 0 and actual < 0))
            bucket["edge"] += score * actual

    output = []
    for name, stat in stats.items():
        n = int(stat["samples"])
        if n <= 0:
            continue
        output.append(
            {
                "factor": name,
                "samples": n,
                "direction_accuracy": stat["correct"] / n,
                "avg_signed_edge": stat["edge"] / n,
            }
        )
    if not output:
        return pd.DataFrame(columns=["factor", "samples", "direction_accuracy", "avg_signed_edge"])
    return pd.DataFrame(output).sort_values(
        ["direction_accuracy", "samples"], ascending=[False, False]
    ).reset_index(drop=True)


def alpha_history(results_dir: str | Path, limit: int = 200) -> pd.DataFrame:
    with _connect(results_dir) as conn:
        rows = conn.execute(
            "SELECT * FROM alpha_forecasts ORDER BY target_close_at DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
    return pd.DataFrame([dict(row) for row in rows])
