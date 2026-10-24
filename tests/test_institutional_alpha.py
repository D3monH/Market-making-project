from __future__ import annotations

import numpy as np
import pandas as pd

from quant_mm.alpha_store import factor_leaderboard, persist_alpha, settle_due_alpha
from quant_mm.institutional_alpha import build_institutional_alpha


def synthetic_market(rows: int = 300, drift: float = 0.0005) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=rows, freq="15min", tz="UTC")
    x = np.arange(rows, dtype=float)
    returns = drift + 0.0005 * np.sin(x / 11.0)
    close = 100.0 * np.cumprod(1.0 + returns)
    open_ = close / (1.0 + returns * 0.4)
    high = np.maximum(open_, close) * 1.0015
    low = np.minimum(open_, close) * 0.9985
    volume = 1000.0 + 50.0 * np.sin(x / 7.0)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def derivative_context() -> dict:
    stats = []
    for i in range(20):
        stats.append(
            {
                "open_interest_usd": 1_000_000 + i * 10_000,
                "lsr_account": 1.10,
            }
        )
    funding = [{"r": str(0.00005 + i * 0.000001)} for i in range(20)]
    return {
        "available": True,
        "ticker": {
            "funding_rate": "0.00012",
            "last": "101.0",
            "index_price": "100.8",
            "mark_price": "100.9",
            "highest_size": "120",
            "lowest_size": "80",
        },
        "stats": stats,
        "funding": funding,
        "errors": [],
    }


def test_institutional_alpha_returns_bounded_independent_scores():
    alpha = build_institutional_alpha(
        synthetic_market(),
        expected_return_pct=0.20,
        model_confidence=0.70,
        derivatives_context=derivative_context(),
        market_regime="TREND_UP",
    )

    assert -1.0 <= alpha.composite_score <= 1.0
    assert -1.0 <= alpha.consensus_score <= 1.0
    assert 0.0 <= alpha.consensus_confidence <= 1.0
    assert 0.45 <= alpha.risk_multiplier <= 1.0
    assert 0.0 <= alpha.data_quality <= 1.0
    assert {"momentum", "trend", "mean_reversion"}.issubset(alpha.factor_scores)
    assert {"carry_crowding", "open_interest", "basis_crowding"}.issubset(alpha.factor_scores)
    assert abs(sum(alpha.factor_weights.values()) - 1.0) < 1e-9


def test_alpha_can_run_price_only_when_derivatives_unavailable():
    alpha = build_institutional_alpha(
        synthetic_market(drift=-0.0004),
        expected_return_pct=-0.15,
        model_confidence=0.62,
        derivatives_context=None,
        market_regime="TREND_DOWN",
    )
    assert alpha.source == "price_only"
    assert set(alpha.factor_scores) == {"momentum", "trend", "mean_reversion"}
    assert alpha.data_quality >= 0.60


def test_alpha_store_deduplicates_same_target_and_settles(tmp_path):
    as_of = pd.Timestamp("2026-10-24T12:00:00Z")
    alpha = build_institutional_alpha(
        synthetic_market(),
        expected_return_pct=0.25,
        model_confidence=0.70,
        derivatives_context=derivative_context(),
        market_regime="TREND_UP",
    ).to_dict()

    first = persist_alpha(
        tmp_path,
        exchange="gate",
        symbol="BTC/USDT",
        timeframe="15m",
        as_of=as_of,
        base_price=100.0,
        model_expected_return_pct=0.25,
        model_confidence=0.70,
        alpha=alpha,
    )
    duplicate = persist_alpha(
        tmp_path,
        exchange="gate",
        symbol="BTC/USDT",
        timeframe="15m",
        as_of=as_of,
        base_price=100.0,
        model_expected_return_pct=0.25,
        model_confidence=0.70,
        alpha=alpha,
    )
    assert first == 1
    assert duplicate == 0

    def candle_fetcher(exchange, symbol, timeframe, target_open_at):
        return {"close": 101.0}

    # base candle 12:00-12:15, target 12:15-12:30; verify only after 12:30.
    before = settle_due_alpha(
        tmp_path,
        candle_fetcher,
        now=pd.Timestamp("2026-10-24T12:29:59Z"),
    )
    assert before["settled"] == 0

    after = settle_due_alpha(
        tmp_path,
        candle_fetcher,
        now=pd.Timestamp("2026-10-24T12:30:01Z"),
    )
    assert after["settled"] == 1

    board = factor_leaderboard(
        tmp_path,
        symbol="BTC/USDT",
        timeframe="15m",
    )
    assert not board.empty
    assert set(board.columns) == {"factor", "samples", "direction_accuracy", "avg_signed_edge"}
