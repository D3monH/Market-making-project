from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

DB_FILENAME = "forecast_history.sqlite3"


def _utc_timestamp(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts


def _timeframe_delta(timeframe: str) -> pd.Timedelta:
    value = timeframe.strip().lower()
    if not value or value[-1] not in {"m", "h", "d", "w"}:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    number = int(value[:-1])
    unit = {"m": "min", "h": "h", "d": "D", "w": "W"}[value[-1]]
    return pd.Timedelta(number, unit=unit)


def _db_path(results_dir: str | Path) -> Path:
    out_dir = Path(results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / DB_FILENAME


def _forecast_id(row: dict) -> str:
    """Stable id for one forecast target.

    Re-running the same symbol/timeframe while the same base candle is still the
    latest closed candle must not create another prediction record. The base
    candle open timestamp (`as_of`) uniquely identifies that target candle.
    """
    key = "|".join(
        str(row.get(name, ""))
        for name in ("exchange", "symbol", "timeframe", "as_of")
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    ddl: str,
) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _deduplicate_existing_forecasts(conn: sqlite3.Connection) -> None:
    """Repair databases created before forecasts were keyed by base candle.

    Prefer a settled record if one exists. Otherwise keep the earliest real
    prediction so repeated button clicks cannot rewrite/cherry-pick history.
    """
    groups = conn.execute(
        """
        SELECT exchange, symbol, timeframe, as_of, COUNT(*) AS n
        FROM forecasts
        GROUP BY exchange, symbol, timeframe, as_of
        HAVING COUNT(*) > 1
        """
    ).fetchall()

    for group in groups:
        rows = conn.execute(
            """
            SELECT rowid, status, forecast_run_at
            FROM forecasts
            WHERE exchange = ? AND symbol = ? AND timeframe = ? AND as_of = ?
            ORDER BY
                CASE WHEN status = 'SETTLED' THEN 0 ELSE 1 END,
                forecast_run_at ASC,
                rowid ASC
            """,
            (group["exchange"], group["symbol"], group["timeframe"], group["as_of"]),
        ).fetchall()
        if len(rows) <= 1:
            continue
        keep_rowid = rows[0]["rowid"]
        conn.execute(
            """
            DELETE FROM forecasts
            WHERE exchange = ? AND symbol = ? AND timeframe = ? AND as_of = ?
              AND rowid <> ?
            """,
            (
                group["exchange"],
                group["symbol"],
                group["timeframe"],
                group["as_of"],
                keep_rowid,
            ),
        )


def _connect(results_dir: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(results_dir))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS forecasts (
            forecast_id TEXT PRIMARY KEY,
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            forecast_run_at TEXT NOT NULL,
            as_of TEXT NOT NULL,
            target_open_at TEXT NOT NULL,
            target_close_at TEXT NOT NULL,
            base_price REAL NOT NULL,
            expected_return_pct REAL NOT NULL,
            expected_price REAL NOT NULL,
            lower_price REAL,
            upper_price REAL,
            up_probability REAL,
            confidence REAL,
            trend TEXT,
            research_signal TEXT,
            validation_direction_accuracy REAL,
            model_predictions_json TEXT,
            model_weights_json TEXT,
            candle_basis TEXT NOT NULL DEFAULT 'legacy_exchange_native',
            status TEXT NOT NULL DEFAULT 'PENDING',
            settled_at TEXT,
            actual_close REAL,
            actual_return_pct REAL,
            direction_correct INTEGER,
            signal_correct INTEGER,
            price_abs_error_pct REAL,
            return_abs_error_pct REAL,
            interval_hit INTEGER,
            raw_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_forecasts_due
            ON forecasts(status, target_close_at);
        CREATE INDEX IF NOT EXISTS idx_forecasts_symbol_tf
            ON forecasts(symbol, timeframe, forecast_run_at);

        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )

    _ensure_column(
        conn,
        "forecasts",
        "candle_basis",
        "candle_basis TEXT NOT NULL DEFAULT 'legacy_exchange_native'",
    )

    _deduplicate_existing_forecasts(conn)
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_forecast_base_candle
        ON forecasts(exchange, symbol, timeframe, as_of)
        """
    )
    return conn


def _prepare_row(row: dict) -> dict:
    timeframe = str(row["timeframe"])
    as_of = _utc_timestamp(row["as_of"])
    delta = _timeframe_delta(timeframe)

    # OHLCV candle timestamps are candle OPEN timestamps. If `as_of` is 20:00
    # for a 15m candle, its close is 20:15. We use that 20:15 close as the base
    # price and predict the next candle, which closes at 20:30.
    target_open = as_of + delta
    target_close = target_open + delta

    prepared = dict(row)
    prepared["forecast_id"] = row.get("forecast_id") or _forecast_id(row)
    prepared["as_of"] = as_of.isoformat()
    prepared["target_open_at"] = target_open.isoformat()
    prepared["target_close_at"] = target_close.isoformat()
    prepared["candle_basis"] = str(
        row.get("candle_basis") or "legacy_exchange_native"
    )
    return prepared


def persist_forecasts(rows: Iterable[dict], results_dir: str | Path) -> int:
    payload = [_prepare_row(dict(row)) for row in rows]
    if not payload:
        return 0

    inserted = 0
    with _connect(results_dir) as conn:
        for row in payload:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO forecasts (
                    forecast_id, exchange, symbol, timeframe, forecast_run_at, as_of,
                    target_open_at, target_close_at, base_price, expected_return_pct,
                    expected_price, lower_price, upper_price, up_probability, confidence,
                    trend, research_signal, validation_direction_accuracy,
                    model_predictions_json, model_weights_json, candle_basis, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["forecast_id"],
                    str(row.get("exchange", "")),
                    str(row.get("symbol", "")),
                    str(row["timeframe"]),
                    str(row.get("forecast_run_at", datetime.now(timezone.utc).isoformat())),
                    row["as_of"],
                    row["target_open_at"],
                    row["target_close_at"],
                    float(row["current_price"]),
                    float(row["expected_return_pct"]),
                    float(row["expected_price"]),
                    float(row.get("lower_price", row["expected_price"])),
                    float(row.get("upper_price", row["expected_price"])),
                    float(row.get("up_probability", 0.5)),
                    float(row.get("confidence", 0.0)),
                    str(row.get("trend", "")),
                    str(row.get("research_signal", "")),
                    float(row.get("validation_direction_accuracy", 0.0)),
                    json.dumps(row.get("model_predictions_pct", {}), ensure_ascii=False),
                    json.dumps(row.get("model_weights", {}), ensure_ascii=False),
                    row["candle_basis"],
                    json.dumps(row, ensure_ascii=False),
                ),
            )
            inserted += int(cursor.rowcount > 0)
    return inserted


def migrate_legacy_jsonl(results_dir: str | Path) -> int:
    """One-time import of older forecast_history.jsonl records into SQLite."""
    out_dir = Path(results_dir)
    path = out_dir / "forecast_history.jsonl"
    if not path.exists():
        return 0

    with _connect(results_dir) as conn:
        marker = conn.execute(
            "SELECT value FROM metadata WHERE key = 'legacy_jsonl_migrated'"
        ).fetchone()
        if marker:
            return 0

    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            required = {"timeframe", "as_of", "current_price", "expected_return_pct", "expected_price"}
            if required.issubset(row):
                rows.append(row)

    inserted = persist_forecasts(rows, results_dir)
    with _connect(results_dir) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('legacy_jsonl_migrated', ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
    return inserted


def load_forecast_records(
    results_dir: str | Path,
    limit: int = 500,
    symbol: str | None = None,
) -> pd.DataFrame:
    migrate_legacy_jsonl(results_dir)
    sql = "SELECT * FROM forecasts"
    params: list[object] = []
    if symbol:
        sql += " WHERE symbol = ?"
        params.append(symbol)
    sql += " ORDER BY target_close_at DESC LIMIT ?"
    params.append(max(1, int(limit)))
    with _connect(results_dir) as conn:
        rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame([dict(row) for row in rows])


def pending_due_forecasts(
    results_dir: str | Path,
    now: datetime | pd.Timestamp | None = None,
    limit: int = 100,
) -> list[dict]:
    migrate_legacy_jsonl(results_dir)
    now_ts = _utc_timestamp(now or datetime.now(timezone.utc)).isoformat()
    with _connect(results_dir) as conn:
        rows = conn.execute(
            """
            SELECT * FROM forecasts
            WHERE status = 'PENDING' AND target_close_at <= ?
            ORDER BY target_close_at ASC
            LIMIT ?
            """,
            (now_ts, max(1, int(limit))),
        ).fetchall()
    return [dict(row) for row in rows]


def _sign(value: float, eps: float = 1e-12) -> int:
    if value > eps:
        return 1
    if value < -eps:
        return -1
    return 0


def _signal_correct(signal: str, actual_return_pct: float) -> int | None:
    if signal == "LONG_BIAS":
        return int(actual_return_pct > 0.0)
    if signal == "SHORT_BIAS":
        return int(actual_return_pct < 0.0)
    return None


def settle_due_forecasts(
    results_dir: str | Path,
    candle_fetcher: Callable[[str, str, str, str], dict | None],
    now: datetime | pd.Timestamp | None = None,
    limit: int = 100,
) -> dict:
    due = pending_due_forecasts(results_dir, now=now, limit=limit)
    settled = 0
    errors: list[str] = []
    settled_at = _utc_timestamp(now or datetime.now(timezone.utc)).isoformat()

    with _connect(results_dir) as conn:
        for row in due:
            try:
                candle = candle_fetcher(
                    row["exchange"], row["symbol"], row["timeframe"], row["target_open_at"]
                )
                if candle is None:
                    continue

                actual_close = float(candle["close"])
                base_price = float(row["base_price"])
                actual_return_pct = (actual_close / base_price - 1.0) * 100.0
                expected_return_pct = float(row["expected_return_pct"])
                expected_price = float(row["expected_price"])
                direction_correct = int(_sign(expected_return_pct) == _sign(actual_return_pct))
                signal_correct = _signal_correct(str(row.get("research_signal") or ""), actual_return_pct)
                price_abs_error_pct = abs(expected_price - actual_close) / max(abs(actual_close), 1e-12) * 100.0
                return_abs_error_pct = abs(expected_return_pct - actual_return_pct)
                lower = float(row["lower_price"]) if row["lower_price"] is not None else expected_price
                upper = float(row["upper_price"]) if row["upper_price"] is not None else expected_price
                interval_hit = int(min(lower, upper) <= actual_close <= max(lower, upper))

                conn.execute(
                    """
                    UPDATE forecasts SET
                        status = 'SETTLED', settled_at = ?, actual_close = ?, actual_return_pct = ?,
                        direction_correct = ?, signal_correct = ?, price_abs_error_pct = ?,
                        return_abs_error_pct = ?, interval_hit = ?
                    WHERE forecast_id = ? AND status = 'PENDING'
                    """,
                    (
                        settled_at,
                        actual_close,
                        actual_return_pct,
                        direction_correct,
                        signal_correct,
                        price_abs_error_pct,
                        return_abs_error_pct,
                        interval_hit,
                        row["forecast_id"],
                    ),
                )
                settled += 1
            except Exception as exc:
                errors.append(f"{row['symbol']} {row['timeframe']}: {exc}")

    remaining_due = len(pending_due_forecasts(results_dir, now=now, limit=10_000))
    return {
        "checked": len(due),
        "settled": settled,
        "remaining_due": remaining_due,
        "errors": errors,
    }


def accuracy_summary(results_dir: str | Path, symbol: str | None = None) -> pd.DataFrame:
    migrate_legacy_jsonl(results_dir)
    where = "WHERE status = 'SETTLED'"
    params: list[object] = []
    if symbol:
        where += " AND symbol = ?"
        params.append(symbol)

    sql = f"""
        SELECT
            symbol,
            timeframe,
            candle_basis,
            COUNT(*) AS settled_count,
            AVG(direction_correct) AS direction_accuracy,
            AVG(CASE WHEN signal_correct IS NOT NULL THEN signal_correct END) AS signal_accuracy,
            SUM(CASE WHEN signal_correct IS NOT NULL THEN 1 ELSE 0 END) AS directional_signal_count,
            AVG(price_abs_error_pct) AS price_mae_pct,
            AVG(return_abs_error_pct) AS return_mae_pct,
            AVG(interval_hit) AS interval_hit_rate,
            AVG(confidence) AS avg_confidence
        FROM forecasts
        {where}
        GROUP BY symbol, timeframe, candle_basis
        ORDER BY symbol, timeframe, candle_basis
    """
    with _connect(results_dir) as conn:
        rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame([dict(row) for row in rows])


def store_status(results_dir: str | Path) -> dict:
    migrate_legacy_jsonl(results_dir)
    now_iso = datetime.now(timezone.utc).isoformat()
    with _connect(results_dir) as conn:
        total = conn.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM forecasts WHERE status='PENDING'").fetchone()[0]
        settled = conn.execute("SELECT COUNT(*) FROM forecasts WHERE status='SETTLED'").fetchone()[0]
        due = conn.execute(
            "SELECT COUNT(*) FROM forecasts WHERE status='PENDING' AND target_close_at <= ?",
            (now_iso,),
        ).fetchone()[0]
        next_row = conn.execute(
            "SELECT MIN(target_close_at) FROM forecasts WHERE status='PENDING'"
        ).fetchone()
    return {
        "total": int(total),
        "pending": int(pending),
        "settled": int(settled),
        "due": int(due),
        "next_target_close_at": next_row[0] if next_row and next_row[0] else None,
        "db_path": str(_db_path(results_dir)),
    }
