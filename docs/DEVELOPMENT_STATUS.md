# Development Status / Codex Handoff

Last updated: 2026-10-25

This document is the current handoff snapshot for ChatGPT/Codex or a human developer taking over the repository.

## 1. Project goal

The repository is evolving from a simple market-making experiment into a small crypto quant research platform:

```text
Market data
  -> deterministic candle/time semantics
  -> persistent local OHLCV cache / incremental sync
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

Current weighting layers:

1. chronological validation weights
2. optional realized out-of-sample model weights after the configured minimum sample count
3. optional bounded manual model multipliers (0.25x..2.0x), then renormalization

Current training-sample weighting:

- `recency_half_life_bars: 0` = disabled / equal sample weight
- when enabled, exponential decay is applied only during model fitting
- chronological train/validation order remains unchanged
- validation metrics remain out-of-sample and are not rewritten by manual preferences

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

### G. Persistent local OHLCV cache

File: `quant_mm/market_cache.py`

Default location:

```text
data/cache/ohlcv/<exchange>/<symbol>/<timeframe>.csv
```

Behavior:

- first use: download the requested forecast-history window
- later use: estimate how many candles could have appeared, fetch a small recent overlap, merge and deduplicate
- cached history is retained and grows over time; it is not truncated to the current `bars` or `train_window`
- if the selected tail contains gaps, one recent full-window repair is attempted
- if the exchange temporarily fails, a recent cache can be used only if it still passes the standard stale-data check
- stale cache never produces a formal prediction

Metadata exposed through `DataFrame.attrs`:

- `cache_refresh_mode`: `cold_start`, `incremental`, `gap_repair`, `offline_cache`, or `network_only`
- `cache_rows_total`
- `downloaded_rows_this_run`
- `missing_intervals`
- `cache_path`
- `cache_refresh_error`
- `last_candle_open_at`
- `candle_basis`

The main dashboard, forecast CLI and Institutional Alpha price-model path all use this cache by default.

## 3. Non-negotiable time semantics

OHLCV timestamps represent candle OPEN times.

For a 15m forecast created at Beijing 20:29:

```text
base candle:      20:00 -> 20:15
base price:       20:15 close
forecast target:  20:15 -> 20:30
target value:     20:30 close
```

The live ticker is display/reference information only. It must never replace the model base close.

### Gate 1d policy — current canonical definition

New Gate `1d` forecasts use the **exchange-native daily candle**.

Gate's daily boundary is treated as:

```text
UTC 00:00 -> next UTC 00:00
```

The dashboard displays timestamps in `Asia/Shanghai` (UTC+8), therefore the same Gate candle is shown as:

```text
Beijing 08:00 -> next-day Beijing 08:00
```

Example: if current Beijing time is 2026-10-24 20:00, the latest fully closed Gate daily candle closed at 2026-10-24 08:00 and the next target daily candle closes at 2026-10-25 08:00.

Do **not** rebuild new Gate daily forecasts as Beijing 00:00 -> 24:00.

### Legacy daily cohort

Older releases created `natural_day:Asia/Shanghai` daily forecasts by aggregating six 4h candles from Beijing 00:00 -> 24:00.

Those records are retained only for backward compatibility. `resample_daily_ohlcv()` and the legacy 4h verification path exist so already-persisted predictions can still be graded against the same definition they originally used.

New and legacy daily cohorts must never be mixed when judging model performance.

See `docs/FORECAST_TIME_SEMANTICS.md`.

## 4. Gate OHLCV history-limit behavior

Gate can reject candlestick requests whose starting point is too many interval points in the past. `quant_mm/data.py` therefore contains a venue-aware history cap.

For Gate:

```text
known boundary: 10,000 interval points
safety margin:     100 points
safe maximum:    9,900 points
```

Large requested histories degrade gracefully to the maximum recent valid window. Pagination still moves forward to the newest fully closed candle.

New Gate `1d` forecasts request native `1d` candles directly and do not expand each day into six 4h candles.

Do not bypass the centralized data layer from Streamlit pages.

### 4.1 Gate public API timeout resilience

Observed failure mode on Windows/CCXT:

```text
ccxt.base.errors.RequestTimeout
gate GET https://api.gateio.ws/api/v4/spot/currencies
```

Current policy in `quant_mm/data.py`:

```text
Gate research instance
  -> timeout = 20 seconds
  -> set has['fetchCurrencies'] = False
  -> load market pairs
  -> retry transient CCXT NetworkError / RequestTimeout up to 3 attempts
```

The same bounded retry helper is used by public ticker and OHLCV requests. Parameter, validation and exchange business errors are not retried as network failures.

Institutional Alpha catches exhausted market-data network failures and renders a concise Streamlit error instead of exposing a full traceback. If the run fails before a forecast is successfully built, no new forecast/alpha record is persisted.

## 5. Data integrity protections already implemented

- Gate legacy exchange id `gateio` normalizes to CCXT `gate`.
- Pagination must reach current closed candles; do not stop simply because requested row count was reached.
- Stale latest candles are rejected.
- In-progress candles are excluded from forecast inputs.
- Duplicate forecasts for the same target are prevented.
- New Gate 1d forecast basis is `gate_native_utc_day`.
- Legacy `natural_day:Asia/Shanghai` records remain historical compatibility data and must stay separate from new daily statistics.
- Transient public API timeouts are retried centrally.
- Persistent OHLCV cache is incremental and deduplicated.
- Offline cache fallback is allowed only when recency validation still passes.
- Manual model multipliers are bounded and do not rewrite realized historical scores.
- Recency sample weighting preserves chronological validation and does not introduce lookahead.

## 6. Persistent storage

### Forecast DB

`results/forecast/forecast_history.sqlite3`

Long-lived source of truth for forecast out-of-sample performance.

### Alpha DB

`results/forecast/alpha_history.sqlite3`

Long-lived source of truth for factor/consensus out-of-sample performance.

### Market cache

`data/cache/ohlcv/...`

Long-lived public OHLCV working set for training/research. It is local derived market data, not a replacement for the forecast/alpha verification databases.

Do not delete SQLite databases as a normal migration strategy. Cache files may be rebuilt from public market data if necessary, but should normally be preserved so local history can grow.

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

Important regression files:

- `tests/test_data_recency.py`
- `tests/test_gate_history_limit.py`
- `tests/test_gate_timeout_resilience.py`
- `tests/test_market_cache.py`
- `tests/test_forecast.py`
- `tests/test_forecast_custom_weights.py`
- `tests/test_forecast_time_alignment.py`
- `tests/test_forecast_store.py`
- `tests/test_model_performance.py`
- `tests/test_institutional_alpha.py`
- `tests/test_v02.py`

Do not merge changes to data/time/persistence/model-weight semantics without a test that demonstrates the expected invariant.

## 9. Recommended next development order

### P0 - stability / observability

Completed items that should not regress:

- explicit candle-basis labels
- Gate timeout/currency-skip policy
- structured Streamlit market-data errors
- persistent incremental OHLCV cache
- cache health metadata in dashboard / Institutional Alpha
- stale-cache rejection
- optional recency weighting and bounded manual model multipliers

Remaining P0:

- background cache prefetch/sync utility for configured symbols/timeframes
- cache health summary page across all symbols/timeframes
- checksum/version metadata for local cache files
- SQLite schema version metadata

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
5. Local cache cannot silently serve stale data for a formal forecast.
6. Manual/model weighting changes remain bounded and observable.
7. Tests are added/updated.
8. `python -m pytest -q` passes.
9. Relevant documentation and `CHANGELOG.md` are updated.
10. PR CI is green before merge.
