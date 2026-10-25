from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

DB_FILENAME = "event_contract_history.sqlite3"


def _db_path(results_dir: str | Path) -> Path:
    root = Path(results_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root / DB_FILENAME


def _utc(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def event_id(asset: str, timeframe: str, benchmark: str, event_open_at) -> str:
    key = f"gate|{asset.upper()}|{timeframe}|{benchmark.upper()}|{_utc(event_open_at).isoformat()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def _connect(results_dir: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(results_dir))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS event_predictions (
            event_id TEXT PRIMARY KEY,
            exchange TEXT NOT NULL DEFAULT 'gate',
            asset TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            benchmark TEXT NOT NULL,
            data_mode TEXT NOT NULL,
            forecast_run_at TEXT NOT NULL,
            event_open_at TEXT NOT NULL,
            event_close_at TEXT NOT NULL,
            model_base_price REAL NOT NULL,
            expected_price REAL NOT NULL,
            expected_return_pct REAL NOT NULL,
            raw_up_probability REAL NOT NULL,
            final_up_probability REAL NOT NULL,
            final_down_probability REAL NOT NULL,
            predicted_side TEXT NOT NULL,
            confidence REAL NOT NULL,
            alpha_score REAL,
            alpha_confidence REAL,
            alpha_data_quality REAL,
            up_market_price REAL,
            down_market_price REAL,
            selected_side TEXT,
            selected_price REAL,
            expected_edge REAL,
            expected_roi_pct REAL,
            status TEXT NOT NULL,
            settled_at TEXT,
            actual_reference_price REAL,
            actual_settlement_price REAL,
            actual_side TEXT,
            direction_correct INTEGER,
            brier_score REAL,
            log_loss REAL,
            realized_pnl_per_share REAL,
            raw_json TEXT NOT NULL,
            UNIQUE(exchange, asset, timeframe, benchmark, event_open_at)
        );
        CREATE INDEX IF NOT EXISTS idx_event_due
            ON event_predictions(status, event_close_at);
        CREATE INDEX IF NOT EXISTS idx_event_cohort
            ON event_predictions(asset, timeframe, benchmark, forecast_run_at);
        """
    )
    return conn


def persist_event_prediction(row: dict, results_dir: str | Path) -> tuple[str, bool]:
    event_open = _utc(row["event_open_at"])
    event_close = _utc(row["event_close_at"])
    benchmark = str(row["benchmark"]).upper()
    asset = str(row["asset"]).upper()
    eid = row.get("event_id") or event_id(asset, row["timeframe"], benchmark, event_open)
    status = str(row.get("status") or ("PENDING" if benchmark == "INDEX" else "RESEARCH_ONLY"))

    selected_side = str(row.get("selected_side") or "WAIT")
    up_market_price = row.get("up_market_price")
    down_market_price = row.get("down_market_price")
    selected_price = None
    if selected_side == "UP" and up_market_price is not None:
        selected_price = float(up_market_price)
    elif selected_side == "DOWN" and down_market_price is not None:
        selected_price = float(down_market_price)

    payload = dict(row)
    payload["event_id"] = eid
    payload["event_open_at"] = event_open.isoformat()
    payload["event_close_at"] = event_close.isoformat()
    payload["status"] = status

    with _connect(results_dir) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO event_predictions (
                event_id, exchange, asset, symbol, timeframe, benchmark, data_mode,
                forecast_run_at, event_open_at, event_close_at, model_base_price,
                expected_price, expected_return_pct, raw_up_probability,
                final_up_probability, final_down_probability, predicted_side,
                confidence, alpha_score, alpha_confidence, alpha_data_quality,
                up_market_price, down_market_price, selected_side, selected_price,
                expected_edge, expected_roi_pct, status, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                eid,
                "gate",
                asset,
                str(row.get("symbol") or f"{asset}/USDT"),
                str(row["timeframe"]),
                benchmark,
                str(row.get("data_mode") or benchmark),
                str(row.get("forecast_run_at") or datetime.now(timezone.utc).isoformat()),
                event_open.isoformat(),
                event_close.isoformat(),
                float(row["model_base_price"]),
                float(row["expected_price"]),
                float(row["expected_return_pct"]),
                float(row["raw_up_probability"]),
                float(row["final_up_probability"]),
                float(row["final_down_probability"]),
                str(row["predicted_side"]),
                float(row.get("confidence", 0.0)),
                None if row.get("alpha_score") is None else float(row["alpha_score"]),
                None if row.get("alpha_confidence") is None else float(row["alpha_confidence"]),
                None if row.get("alpha_data_quality") is None else float(row["alpha_data_quality"]),
                None if up_market_price is None else float(up_market_price),
                None if down_market_price is None else float(down_market_price),
                selected_side,
                selected_price,
                None if row.get("expected_edge") is None else float(row["expected_edge"]),
                None if row.get("expected_roi_pct") is None else float(row["expected_roi_pct"]),
                status,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        return eid, bool(cursor.rowcount > 0)


def load_event_records(
    results_dir: str | Path,
    *,
    asset: str | None = None,
    benchmark: str | None = None,
    limit: int = 300,
) -> pd.DataFrame:
    clauses: list[str] = []
    params: list[object] = []
    if asset:
        clauses.append("asset = ?")
        params.append(str(asset).upper())
    if benchmark:
        clauses.append("benchmark = ?")
        params.append(str(benchmark).upper())
    sql = "SELECT * FROM event_predictions"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY event_close_at DESC LIMIT ?"
    params.append(max(1, int(limit)))
    with _connect(results_dir) as conn:
        rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def get_existing_event(
    results_dir: str | Path,
    *,
    asset: str,
    timeframe: str,
    benchmark: str,
    event_open_at,
) -> dict | None:
    eid = event_id(asset, timeframe, benchmark, event_open_at)
    with _connect(results_dir) as conn:
        row = conn.execute("SELECT * FROM event_predictions WHERE event_id = ?", (eid,)).fetchone()
    return None if row is None else dict(row)


def pending_due_index_events(
    results_dir: str | Path,
    *,
    now=None,
    limit: int = 100,
) -> list[dict]:
    now_iso = _utc(now or datetime.now(timezone.utc)).isoformat()
    with _connect(results_dir) as conn:
        rows = conn.execute(
            """
            SELECT * FROM event_predictions
            WHERE benchmark = 'INDEX' AND status = 'PENDING' AND event_close_at <= ?
            ORDER BY event_close_at ASC LIMIT ?
            """,
            (now_iso, max(1, int(limit))),
        ).fetchall()
    return [dict(r) for r in rows]


def _settlement_metrics(row: dict, actual_up: bool, actual_reference: float | None, actual_close: float | None) -> dict:
    p_up = min(max(float(row["final_up_probability"]), 1e-9), 1.0 - 1e-9)
    y = 1.0 if actual_up else 0.0
    predicted = str(row["predicted_side"])
    actual_side = "UP" if actual_up else "DOWN"
    direction_correct = None if predicted == "WAIT" else int(predicted == actual_side)
    brier = (p_up - y) ** 2
    log_loss = -(y * math.log(p_up) + (1.0 - y) * math.log(1.0 - p_up))

    selected = str(row.get("selected_side") or "WAIT")
    selected_price = row.get("selected_price")
    pnl = None
    if selected in {"UP", "DOWN"} and selected_price is not None:
        won = (selected == "UP" and actual_up) or (selected == "DOWN" and not actual_up)
        price = float(selected_price)
        pnl = (1.0 - price) if won else -price

    return {
        "actual_reference_price": actual_reference,
        "actual_settlement_price": actual_close,
        "actual_side": actual_side,
        "direction_correct": direction_correct,
        "brier_score": brier,
        "log_loss": log_loss,
        "realized_pnl_per_share": pnl,
    }


def settle_due_index_events(
    results_dir: str | Path,
    candle_fetcher: Callable[[str, str, object], dict | None],
    *,
    now=None,
    limit: int = 100,
) -> dict:
    due = pending_due_index_events(results_dir, now=now, limit=limit)
    settled = 0
    errors: list[str] = []
    settled_at = _utc(now or datetime.now(timezone.utc)).isoformat()

    with _connect(results_dir) as conn:
        for row in due:
            try:
                candle = candle_fetcher(row["asset"], row["timeframe"], row["event_open_at"])
                if not candle:
                    continue
                reference = float(candle["open"])
                close = float(candle["close"])
                actual_up = close >= reference
                metrics = _settlement_metrics(row, actual_up, reference, close)
                conn.execute(
                    """
                    UPDATE event_predictions SET
                        status = 'SETTLED', settled_at = ?, actual_reference_price = ?,
                        actual_settlement_price = ?, actual_side = ?, direction_correct = ?,
                        brier_score = ?, log_loss = ?, realized_pnl_per_share = ?
                    WHERE event_id = ? AND status = 'PENDING'
                    """,
                    (
                        settled_at,
                        metrics["actual_reference_price"],
                        metrics["actual_settlement_price"],
                        metrics["actual_side"],
                        metrics["direction_correct"],
                        metrics["brier_score"],
                        metrics["log_loss"],
                        metrics["realized_pnl_per_share"],
                        row["event_id"],
                    ),
                )
                settled += 1
            except Exception as exc:
                errors.append(f"{row['asset']} {row['timeframe']}: {exc}")

    return {"checked": len(due), "settled": settled, "errors": errors}


def manually_settle_event(
    results_dir: str | Path,
    event_id_value: str,
    *,
    actual_side: str,
    actual_reference_price: float | None = None,
    actual_settlement_price: float | None = None,
) -> bool:
    side = str(actual_side).upper()
    if side not in {"UP", "DOWN"}:
        raise ValueError("actual_side must be UP or DOWN")
    with _connect(results_dir) as conn:
        row = conn.execute("SELECT * FROM event_predictions WHERE event_id = ?", (event_id_value,)).fetchone()
        if row is None:
            return False
        row_dict = dict(row)
        metrics = _settlement_metrics(
            row_dict,
            side == "UP",
            actual_reference_price,
            actual_settlement_price,
        )
        conn.execute(
            """
            UPDATE event_predictions SET status = 'SETTLED_MANUAL', settled_at = ?,
                actual_reference_price = ?, actual_settlement_price = ?, actual_side = ?,
                direction_correct = ?, brier_score = ?, log_loss = ?, realized_pnl_per_share = ?
            WHERE event_id = ?
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                metrics["actual_reference_price"],
                metrics["actual_settlement_price"],
                metrics["actual_side"],
                metrics["direction_correct"],
                metrics["brier_score"],
                metrics["log_loss"],
                metrics["realized_pnl_per_share"],
                event_id_value,
            ),
        )
    return True


def event_accuracy_summary(results_dir: str | Path, *, asset: str | None = None) -> pd.DataFrame:
    clauses = ["status IN ('SETTLED', 'SETTLED_MANUAL')"]
    params: list[object] = []
    if asset:
        clauses.append("asset = ?")
        params.append(str(asset).upper())
    sql = f"""
        SELECT asset, timeframe, benchmark,
               COUNT(*) AS samples,
               AVG(direction_correct) AS direction_accuracy,
               AVG(brier_score) AS brier_score,
               AVG(log_loss) AS log_loss,
               AVG(confidence) AS avg_confidence,
               AVG(realized_pnl_per_share) AS avg_realized_pnl_per_share
        FROM event_predictions
        WHERE {' AND '.join(clauses)}
        GROUP BY asset, timeframe, benchmark
        ORDER BY asset, timeframe, benchmark
    """
    with _connect(results_dir) as conn:
        rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame([dict(r) for r in rows])
