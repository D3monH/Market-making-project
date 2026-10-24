# Changelog

## 2026-10-24 - Gate history-limit stability hotfix

- Fixed Gate `INVALID_PARAM_VALUE: Candlestick too long ago` failures affecting both the main forecast dashboard and Institutional Alpha page.
- Added venue-aware OHLCV lookback limits in `quant_mm/data.py`.
- Gate requests are kept inside a 10,000-point boundary with a 100-point safety margin.
- Very large history requests now degrade to the maximum recent valid window instead of failing.
- Added DataFrame metadata describing requested/returned history and whether a venue limit reduced the result.
- Added `tests/test_gate_history_limit.py`.
- Added root `AGENTS.md` for Codex/agent takeover.
- Added `docs/DEVELOPMENT_STATUS.md` as the current architecture, invariants, known limitations and roadmap handoff.

## 2026-10-24 - Institutional Alpha Engine (PR #11)

- Added independent price/derivatives alpha layer.
- Added momentum, trend, mean reversion and volatility risk factors.
- Added Gate public Funding, Open Interest, Basis, positioning and top-of-book factors.
- Added regime-aware factor weights.
- Added Institutional Alpha Streamlit page.
- Added persistent alpha SQLite verification and per-factor leaderboard.
- Kept alpha consensus independent from the trained next-close forecast until enough out-of-sample evidence exists.

## 2026-10-24 - Advanced ensemble and realized model weights (PR #10)

- Expanded regression ensemble with ElasticNet and ExtraTrees.
- Added market-regime labels.
- Added per-model out-of-sample scoring and model leaderboard.
- Added blending of chronological validation weights with realized model performance after a minimum settled-sample threshold.

## 2026-10-24 - Forecast time semantics hardening (PR #9)

- Standardized display and forecast boundaries to explicit timezone semantics.
- Rebuilt `1d` forecasts as natural `Asia/Shanghai` days from 4h candles.
- Added stale-market-data rejection.
- Added candle-basis versioning so legacy UTC/native daily records are not silently mixed with Beijing-natural daily records.

## 2026-10-24 - Base-candle uniqueness and chart alignment (PR #8)

- Enforced one forecast per exchange + symbol + timeframe + base candle.
- Fixed repeated-button duplicate forecast records.
- Confirmed model base price equals the latest fully closed candle close.
- Corrected chart timestamps to represent candle close times for forecast interpretation.

## 2026-10-24 - Persistent forecast verification (PR #7)

- Added SQLite forecast persistence.
- Added target-candle maturity checks and automatic verification.
- Added realized direction, return, price error and prediction-interval metrics.
- Added long-running forecast verification monitor.

## 2026-10-24 - Latest-candle pagination fix (PR #6)

- Fixed historical pagination that could stop after collecting enough rows before reaching current data.
- Prevented old historical close values from being shown as current forecast base data.

## 2026-10-24 - Gate exchange alias fix (PR #5)

- Normalized legacy `gateio` configuration values to CCXT exchange id `gate`.

## 2026-10-24 - Streamlit dashboard (PR #4)

- Added local multi-horizon forecast dashboard and Windows startup script.

## Earlier baseline

- Historical forecast engine and multi-timeframe models.
- Gate realtime public paper market-making pipeline.
- Historical market-making backtest baseline.
