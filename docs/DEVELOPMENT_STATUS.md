# Development Status / Codex Handoff

Last updated: 2026-10-24

This document is the current handoff snapshot for ChatGPT/Codex or a human developer taking over the repository.

## 1. Project goal

The repository is evolving from a simple market-making experiment into a small crypto quant research platform:

```text
Market data
  -> deterministic candle/time semantics
  -> feature/model research
  -> multi-horizon next-close forecasts
  -> persistent out-of-sample verification
  -> model leaderboard / dynamic weights
  -> independent institutional-style alpha factors
  -> paper/testnet execution research
```

The priority is research correctness and reproducibility before live execution.

## 2. Current functional baseline

### A. Historical market-making backtest

Files: `main.py`, `quant_mm/backtest.py`, `quant_mm/market_maker.py`

- CCXT OHLCV input
- linear signal shifts reservation price
- inventory skew
- volatility-sensitive spread
- passive OHLCV fill approximation
- fees / equity / drawdown / Sharpe

Known limitation: OHLCV backtests cannot represent true queue position, latency or intrabar event order.

### B. Gate realtime paper market making

Files: `gate_paper.py`, `quant_mm/gate_stream.py`, `quant_mm/paper_live.py`, `quant_mm/online_model.py`

- Gate public `book_ticker` and trades
- top-of-book imbalance + trade-flow alpha
- causal online LinearRegression
- strict-crossing paper fill rule
- spot inventory / no synthetic short by default
- daily-loss and drawdown kill switches

This is paper-only. It is not production execution.

### C. Multi-horizon forecast engine

Files: `forecast.py`, `dashboard.py`, `quant_mm/forecast_engine.py`

Supported research horizons include 5m / 15m / 30m / 1h / 4h / 12h / 1d.

Regression ensemble currently contains:

- Linear Regression
- Ridge
- ElasticNet
- HistGradientBoosting
- ExtraTrees

A Logistic Regression classifier separately estimates next-candle `P(up)`.

Outputs include:

- expected next-candle return
- expected next close
- prediction interval
- up probability
- trend class
- research signal
- confidence
- validation MAE / direction accuracy
- market regime
- per-model predictions and weights

### D. Persistent forecast verification

Files: `quant_mm/forecast_store.py`, `forecast_monitor.py`

Database: `results/forecast/forecast_history.sqlite3`

For each prediction the system stores:

- exchange / symbol / timeframe
- forecast timestamp
- base candle open timestamp
- base price
- target candle open/close timestamp
- model predictions / weights
- prediction interval / probability / confidence
- maturity status
- realized close / return
- direction hit
- price and return errors
- interval hit

Uniqueness is enforced per exchange + symbol + timeframe + base candle.

### E. Realized model leaderboard

File: `quant_mm/model_performance.py`

After enough settled out-of-sample samples, realized model performance can be blended with chronological validation weights.

Default minimum realized sample count: 30.

### F. Institutional Alpha page

Files: `pages/01_Institutional_Alpha.py`, `quant_mm/institutional_alpha.py`, `quant_mm/alpha_store.py`

Independent factors currently include:

Price factors:
- momentum
- trend
- mean reversion
- volatility risk multiplier

Gate public derivatives context:
- funding/carry crowding
- open-interest confirmation
- perpetual premium/basis crowding
- long/short account positioning
- top-of-book imbalance

The alpha layer intentionally does not rewrite the forecasted next close. It produces a separate consensus/risk overlay so new factors can prove themselves out-of-sample first.

Database: `results/forecast/alpha_history.sqlite3`

## 3. Non-negotiable time semantics

OHLCV timestamps represent candle OPEN times.

For a 15m forecast created at Beijing 20:29:

```text
base candle:   20:00 -> 20:15
base price:    20:15 close
forecast target: 20:15 -> 20:30
target value:  20:30 close
```

The live ticker is display/reference information only. It must never replace the model base close.

### 1d policy

`1d` means the natural `Asia/Shanghai` day:

```text
00:00 Beijing -> next day 00:00 Beijing
```

It is rebuilt from six closed 4h candles, rather than using the exchange-native UTC daily candle.

Training, prediction and verification must all use the same daily definition.

See `docs/FORECAST_TIME_SEMANTICS.md`.

## 4. Gate OHLCV history-limit hotfix

### Symptom

Selecting 5,000 historical K lines and forecasting `1d` caused both the main dashboard and Institutional Alpha page to fail with:

```text
INVALID_PARAM_VALUE
Candlestick too long ago. Maximum 10000 points ago are allowed
```

### Root cause

A Beijing-natural 1d candle is rebuilt from six 4h candles.

Requesting 5,000 daily candles therefore asked for roughly:

```text
5000 * 6 + padding ~= 30,000 4h candles
```

The old `fetch_ohlcv()` also applied a 10% gap buffer, which pushed `since` even farther into the past.

Gate rejects a candlestick request whose start time is too many interval-points in the past.

### Fix

`quant_mm/data.py` now contains a venue-aware maximum OHLCV lookback table.

For Gate:

```text
known boundary: 10,000 interval points
safety margin:     100 points
safe maximum:    9,900 points
```

A huge requested history now degrades gracefully to the maximum recent valid source window. The function still paginates forward to the newest fully closed candles.

For `1d`, a request for 5,000 days on Gate therefore produces the maximum number of complete Beijing-natural days that can be reconstructed from the available recent 4h history (roughly 1,600+ days, depending on boundaries/gaps), instead of throwing HTTP 400.

The returned DataFrame contains metadata in `df.attrs`:

- `requested_bars`
- `returned_bars`
- `venue_history_limit_points`
- `history_limited`
- for 1d: source timeframe/requested/returned bars

Do not remove this protection unless the venue API behavior is re-verified.

## 5. Data integrity protections already implemented

- Gate legacy exchange id `gateio` normalizes to CCXT `gate`.
- Pagination must reach current closed candles; do not stop simply because requested row count was reached.
- Stale latest candles are rejected.
- In-progress candles are excluded from forecast inputs.
- Duplicate forecasts for the same target are prevented.
- 1d daily-basis version is stored in persistence (`natural_day:Asia/Shanghai`).
- Legacy records keep their old basis and are not silently mixed into new daily statistics.

## 6. Current databases

### Forecast DB

`results/forecast/forecast_history.sqlite3`

Long-lived source of truth for forecast out-of-sample performance.

### Alpha DB

`results/forecast/alpha_history.sqlite3`

Long-lived source of truth for factor/consensus out-of-sample performance.

Do not delete these databases as a normal migration strategy. Use schema migrations/backward-compatible columns where practical.

## 7. Gate private execution status

`quant_mm/gate_execution.py` contains a future private REST skeleton with environment-variable credentials and a live confirmation switch.

It is **not** a complete live market-making engine.

Still missing before production use:

- authoritative private websocket reconciliation
- complete order state machine
- restart recovery
- precision/min-size metadata enforcement everywhere
- cancel/replace throttling and rate-limit handling
- stale-data / time-sync guards
- idempotent fill/order reconciliation
- stronger risk and operational monitoring

Preferred progression:

```text
research -> paper -> Gate TestNet -> controlled live only after validation
```

## 8. Tests / CI

Primary command:

```bash
python -m pytest -q
```

GitHub Actions runs the same test suite.

Important regression files:

- `tests/test_data_recency.py`
- `tests/test_gate_history_limit.py`
- `tests/test_forecast.py`
- `tests/test_forecast_time_alignment.py`
- `tests/test_forecast_store.py`
- `tests/test_model_performance.py`
- `tests/test_institutional_alpha.py`
- `tests/test_v02.py`

Do not merge changes to data/time/persistence semantics without a test that demonstrates the expected invariant.

## 9. Recommended next development order

### P0 - stability / observability

- Surface requested vs effective history bars in both Streamlit pages.
- Add data-source health panel: last closed timestamp, returned rows, venue cap, stale status.
- Add migration/version number to SQLite metadata.
- Add structured error categories instead of raw tracebacks in UI.

### P1 - historical derivatives data aligned to candles

Build persistent time-series collectors for:

- Funding
- Open Interest
- Basis/premium
- Liquidations
- Taker buy/sell flow

Important: features must be aligned to what was knowable at the forecast timestamp. Do not backfill a historical feature using future snapshots.

### P2 - cross-asset / market breadth

- BTC/ETH/SOL relative momentum
- rolling beta/correlation
- broad market breadth
- lead/lag tests

### P3 - alpha portfolio

- realized factor weighting
- factor correlation matrix
- shrinkage/caps so one factor cannot dominate
- regime-specific factor weights
- contribution / attribution report

### P4 - additional models

Only after P1/P2 data is stable:

- LightGBM / XGBoost
- quantile regression
- HMM regime classifier
- Kalman filter for relative-value/state estimation

Deep sequence models/Transformers are lower priority until data volume and out-of-sample baselines justify them.

### P5 - execution

- Gate TestNet order manager
- private order/usertrade reconciliation
- cancel/replace state machine
- restart recovery
- execution metrics / markout / adverse selection

## 10. Definition of done for future Codex changes

A substantial change is not done until:

1. Existing forecast time semantics remain correct.
2. No future data leakage is introduced.
3. Persistent forecast uniqueness is preserved.
4. New market-data endpoints fail gracefully.
5. Tests are added/updated.
6. `python -m pytest -q` passes.
7. Relevant documentation and `CHANGELOG.md` are updated.
8. PR CI is green before merge.
