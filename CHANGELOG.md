# Changelog

## 2026-10-25 - Gate event contracts probability research center

- Added `Gate 事件合约` as a dedicated desktop page for crypto event-contract research.
- Added current Gate crypto-event scope: BTC/ETH/SOL/XRP/DOGE/HYPE/BNB and 5m/15m/1h/4h research selection.
- Added official Gate INDEX data support for the currently documented BTC/ETH 5m/15m INDEX event markets through `index_*` futures index candlesticks.
- INDEX OHLC comes from the Gate index; ordinary futures volume is aligned only as a model activity feature.
- Added explicit TWAP research mode. Gate documentation says event TWAP is Chainlink 60-second TWAP; because public API v4 does not document event TWAP history/order books, spot/history proxies are never labeled as official TWAP settlement data.
- Added event probability fusion: logistic P(UP) + regression-implied probability + bounded Institutional Alpha log-odds adjustment + volatility risk shrink.
- Institutional Alpha can use Momentum/Trend/Mean Reversion and public Gate Funding/OI/Basis/positioning/microstructure context to calibrate event probabilities without overwriting the price model.
- Added optional manual Gate UP/DOWN prices and gross Edge/EV/ROI calculations before dynamic trading fees.
- Added independent `results/event_contracts/event_contract_history.sqlite3` persistence with one record per asset + timeframe + benchmark + event open.
- Added automatic INDEX maturity verification from the exact Gate index event candle open/close, including direction accuracy, Brier score, log loss and optional realized PnL/share.
- Added manual TWAP settlement workflow until an official documented Gate event TWAP history API is available.
- Extended the existing background worker to settle matured INDEX event predictions without submitting orders.
- Added `docs/GATE_EVENT_CONTRACTS.md` and event-specific tests.

## 2026-10-25 - Quick direction terminal and persistent auto forecasting

- Reworked `pages/00_Quick_Direction.py` into a wider product-style daily-use terminal with responsive cards instead of narrow `st.metric()` columns that truncate BTC prices and labels.
- The quick page now keeps direction, direction probability, expected return %, quote-asset price change, predicted close, base close, live reference price, confidence, validation accuracy, model agreement, MAE and training-sample count visible without forcing multiple expanders.
- Removed an accidental top-level English string from `app.py` that Streamlit magic rendered above the page.
- Added a recent prediction-history table directly to the quick page.
- Manual repeated clicks for the same exchange + symbol + timeframe + base candle now load the first persisted forecast instead of recomputing a different display-only result.
- Added persistent automatic-forecast settings through `quant_mm/auto_forecast.py` and `results/forecast/auto_forecast.json`.
- Added `auto_forecast_worker.py` as a singleton background checker. Poll frequency only checks for newly closed candles; an unchanged base candle is skipped before model training.
- Updated `start_dashboard.bat` to start/reuse the background worker whenever the desktop platform is launched.
- Automatic forecasts share the same SQLite truth set and duplicate invariant as manual forecasts and may also settle due predictions.
- Added `docs/AUTO_FORECAST.md` and expanded Codex/Desktop UX handoff rules.
- Added `tests/test_auto_forecast.py` for settings persistence, duplicate precheck and new-target generation.

## 2026-10-25 - Chinese desktop UX and one-click launcher

- Added `app.py` as the preferred human-facing Streamlit entrypoint with explicit Chinese navigation: `多周期预测` and `机构 Alpha 研究`.
- Added presentation-only Chinese mapping in `quant_mm/ui_labels.py`; stable database/model identifiers remain unchanged.
- Translated common raw UI values such as `PENDING`, `SETTLED`, `gate_native_utc_day`, `exchange_native_closed`, cache modes and model names into readable Chinese labels.
- Human-facing tables now render missing values as `—` instead of Python `None`.
- Updated `start_dashboard.bat` into a self-initializing launcher: creates `.venv` when absent, installs dependencies when needed, starts `app.py`, and reuses an already-running healthy dashboard on `127.0.0.1:8501`.
- Added `launch_dashboard_hidden.vbs` so desktop launches do not leave a command window visible.
- Added `create_desktop_shortcut.ps1` plus `创建桌面快捷方式.bat`; one setup creates a persistent desktop shortcut named `Crypto量化研究平台`.
- Added `docs/DESKTOP_UX.md` and updated `AGENTS.md` so Codex preserves the Chinese presentation layer and one-click Windows workflow.
- Added `tests/test_ui_labels.py` for label translation, missing-value display and launcher wiring.

## 2026-10-25 - Persistent OHLCV cache and custom training weights

- Added `quant_mm/market_cache.py` as the normal forecast/Alpha/CLI market-data working layer.
- First run downloads the requested history window; later runs fetch only a small recent overlap, merge/deduplicate, and keep the local cache growing over time.
- Default cache path: `data/cache/ohlcv/<exchange>/<symbol>/<timeframe>.csv`.
- Added cache health metadata: refresh mode, total local rows, downloaded rows, missing intervals, last candle and cache path.
- Added recent-cache fallback during temporary exchange outages while preserving the existing stale-data hard rejection.
- Main Dashboard and Institutional Alpha now expose local cache status.
- Added optional exponential recency sample weighting through `recency_half_life_bars`.
- Added optional bounded manual model multipliers (0.25x..2.0x) layered after automatic validation/realized weights, followed by normalization.
- Added Dashboard controls for cache, training window, recency half-life and per-model manual multipliers.
- Forecast CLI now uses the persistent cache by default and supports `--no-cache`.
- Added `tests/test_market_cache.py`, `tests/test_forecast_custom_weights.py`, and an end-to-end weighted advanced-model forecast test.
- Updated `AGENTS.md`, `docs/DEVELOPMENT_STATUS.md`, and `config_forecast.yaml` for Codex handoff.

## 2026-10-24 - Gate public API timeout resilience

- Fixed Institutional Alpha crashes caused by CCXT timing out on Gate `/spot/currencies` during `load_markets()`.
- Gate public research instances now disable CCXT `fetchCurrencies` before market loading because currency metadata is not required for ticker/OHLCV forecasting.
- Added a 20-second CCXT HTTP timeout and three-attempt retry policy for transient `NetworkError` / `RequestTimeout` failures.
- Applied the same retry helper to public ticker and OHLCV requests instead of protecting initialization only.
- Institutional Alpha now converts exhausted network retries into an in-page warning instead of a full Streamlit traceback.
- Network failures do not persist a new formal prediction; existing SQLite history remains intact.
- Added `tests/test_gate_timeout_resilience.py` so future CCXT/Codex changes preserve currency-skip and retry behavior.

## 2026-10-24 - Gate native daily boundary correction

- Corrected new Gate `1d` forecasts to use the exchange-native daily candle instead of a custom Beijing 00:00 -> 24:00 reconstruction.
- Gate daily boundaries are now treated as UTC 00:00 -> next UTC 00:00, displayed as Beijing 08:00 -> next-day 08:00.
- `fetch_forecast_ohlcv()` now uses native `1d` directly; new daily forecasts no longer expand into six 4h source candles.
- Native Gate daily verification now fetches the exact exchange `1d` candle.
- Legacy `natural_day:Asia/Shanghai` records remain compatible: their non-UTC-midnight targets are still verified by the old exact 4h aggregation path.
- Added explicit regression coverage for Beijing 08:00 daily close semantics and native-vs-legacy daily verification.
- Updated `AGENTS.md`, `docs/DEVELOPMENT_STATUS.md`, `docs/FORECAST_TIME_SEMANTICS.md`, and `config_forecast.yaml` so Codex does not reintroduce the old 00:00 daily definition.

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
- Note: the custom Beijing 00:00 daily definition introduced here was superseded later the same day by the Gate native daily boundary correction above.

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
