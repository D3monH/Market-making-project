# AGENTS.md

This repository is an experimental crypto quantitative-research platform. Read `docs/DEVELOPMENT_STATUS.md` before making structural changes.

## Working agreement

- Keep forecasting/research logic separate from live execution.
- Do not add real-order behavior to the dashboard by default.
- Never commit API keys or secrets. Gate private credentials must remain environment variables.
- Preserve existing time semantics and persistence rules; these are data-integrity invariants, not UI preferences.
- Prefer a feature/fix branch + PR + green GitHub Actions before merging.

## Critical forecast invariants

1. OHLCV timestamps are candle OPEN timestamps.
2. A forecast uses the latest fully closed candle close as its base price. Never use the live ticker as the model base price.
3. Example for 15m at Beijing 20:29:
   - base candle: 20:00 -> 20:15
   - base price: 20:15 close
   - target candle: 20:15 -> 20:30
   - target to verify: 20:30 close
4. One exchange + symbol + timeframe + base candle may create only one persistent forecast. Re-clicking must not rewrite/cherry-pick the prediction.
5. Manual and automatic forecast paths must show/use the same first persisted prediction for a target. If a record already exists for the current base candle, do not retrain just to display another result.
6. New Gate 1d forecasts use Gate's exchange-native daily candle. Its boundary is UTC 00:00 -> next UTC 00:00, which displays as Beijing 08:00 -> next-day 08:00 in `Asia/Shanghai`. Do not rebuild new Gate daily forecasts as Beijing 00:00 -> 24:00.
7. Legacy records with `natural_day:Asia/Shanghai` are historical compatibility cohorts only. They may still be settled using the legacy 4h aggregation path, but must never be mixed with the new Gate-native daily cohort.
8. Verification happens only after the complete target candle has closed.
9. Realized model/alpha scores must come only from settled out-of-sample predictions.

## Automatic forecast invariants

- `quant_mm/auto_forecast.py` owns saved auto-forecast settings and a single forecast cycle.
- `auto_forecast_worker.py` is the persistent background worker started by the Windows launcher.
- Poll frequency is only the frequency used to check for a newly closed candle; it is not a forecast horizon.
- Every cycle must check the current base candle against persisted history before model training. Existing targets are skipped without retraining.
- SQLite's existing unique key remains the final duplicate guard; do not weaken it.
- Auto forecasts and manual forecasts share the same `forecast_history.sqlite3` truth set. Do not create a second history database for automatic forecasts.
- Auto forecast settings live under `results/forecast/auto_forecast.json`; worker status is operational metadata in `auto_forecast_status.json`.
- Auto forecasting is research automation only. It must not submit exchange orders or require private API credentials.
- Read `docs/AUTO_FORECAST.md` before changing worker/scheduling behavior.

## Market-data constraints

- All forecast market-data access should flow through `quant_mm/data.py` and, for normal forecast/Alpha UI/CLI usage, through `quant_mm/market_cache.py`.
- Persistent OHLCV cache files live under `data/cache/ohlcv/<exchange>/<symbol>/<timeframe>.csv` by default. First use downloads a requested window; later runs refresh only a small recent overlap, merge, deduplicate and retain the full local history. Do not truncate the cache to the current training-window size.
- A cache may be used during a temporary exchange outage only if the latest cached closed candle still passes the normal recency check. Stale cache must never produce a formal forecast.
- Cache metadata (`cache_refresh_mode`, total rows, downloaded rows, missing intervals, cache path) should remain observable in the research UI.
- Gate spot candlesticks reject excessively old `since/from` windows. The data layer contains a venue-aware history cap and safety margin. Do not bypass it by calling CCXT OHLCV directly from Streamlit pages.
- Gate public research instances intentionally set `exchange.has['fetchCurrencies'] = False` before `load_markets()`. The project only needs market-pair metadata for public ticker/OHLCV research, and `/spot/currencies` has caused avoidable startup timeouts. Do not re-enable it without a concrete requirement and regression coverage.
- Public CCXT network requests use bounded retries for transient `NetworkError` / `RequestTimeout` failures. Do not retry exchange parameter/validation errors as if they were network failures.
- A requested bar count is a preferred target. If a venue cannot supply that much history, use the maximum recent valid window and let the model's minimum-row checks decide whether training is possible.
- Do not reintroduce the old bug where pagination stops as soon as `bars` rows are collected before reaching current closed candles.
- `resample_daily_ohlcv()` exists for legacy-data compatibility; it is not the source for new Gate 1d forecasts.

## Model-weighting constraints

- Automatic validation / realized out-of-sample weights remain the default.
- Optional `recency_half_life_bars` affects model fitting only; it must not leak future information or reorder the chronological validation split.
- Optional `manual_model_multipliers` are a bounded research overlay after automatic weights. Runtime bounds are 0.25x..2.0x and final weights are renormalized.
- Manual preferences must never rewrite historical predictions or realized score history.
- If adding a new estimator, verify that its training path supports sample weights or explicitly documents why it does not.

## UI / desktop experience constraints

- `app.py` is the preferred human-facing Streamlit entrypoint. It provides Chinese navigation and presentation translation while preserving stable internal identifiers.
- `pages/00_Quick_Direction.py` is the default daily-use page. It should answer the next-candle direction question first and keep model/data references visible without forcing the user through multiple expanders.
- `dashboard.py` and `pages/01_Institutional_Alpha.py` remain detailed research pages; do not duplicate model/data semantics in presentation code.
- Quick direction semantics are fixed:
  - predicted close above the previous fully closed candle close => `收涨（多） ↑`;
  - predicted close below the previous fully closed candle close => `收跌（空） ↓`;
  - percentage return is the primary move display;
  - absolute price move is secondary and shown in the quote asset (for example USDT), not as a universal comparable points-return metric;
  - use `P(up)` for an up forecast and `1-P(up)` for a down forecast;
  - surface disagreement between regression direction and the separate probability classifier instead of hiding it.
- The quick page must show recent persisted prediction history and make duplicate semantics clear to the user.
- User-facing labels should be Chinese by default. Keep established domain names such as BTC/ETH, Alpha, Funding, OI, Basis, Linear/Ridge/ExtraTrees when useful for recognition.
- Do not rename persisted database fields/enums merely to make the UI Chinese. Translate at the presentation layer via `quant_mm/ui_labels.py`.
- Human-facing tables should not expose raw values such as `PENDING`, `SETTLED`, `gate_native_utc_day`, `legacy_alpha_unversioned`, `timeframe`, or `actual_return_pct` when a Chinese label exists.
- Missing display values should render as `—`, not Python `None`.
- `start_dashboard.bat` is the canonical one-click launcher. It initializes/reuses the environment, starts the singleton automatic forecast worker, then opens/reuses the dashboard on port 8501.
- `launch_dashboard_hidden.vbs` + `create_desktop_shortcut.ps1` implement the Windows desktop shortcut flow. Keep the shortcut target stable so repository updates do not require recreating it.
- See `docs/DESKTOP_UX.md` before changing launcher/navigation behavior.

## UI error-handling constraints

- Streamlit research pages should not expose a full traceback for routine market-data timeouts.
- A failed network request must not persist a new formal forecast/alpha record.
- Existing SQLite forecast/alpha history should remain readable even when current public APIs are temporarily unavailable.

## Current major components

- `app.py`: preferred Chinese multi-page Streamlit entrypoint.
- `pages/00_Quick_Direction.py`: default fast next-candle direction view, prediction history and auto-forecast controls.
- `start_dashboard.bat`: Windows one-click local launcher; also starts the singleton background worker.
- `auto_forecast_worker.py`: background scheduler/checker for persistent automatic forecasts.
- `quant_mm/auto_forecast.py`: auto settings/status, duplicate precheck and automatic forecast cycle.
- `launch_dashboard_hidden.vbs` / `create_desktop_shortcut.ps1`: desktop shortcut and hidden launcher.
- `main.py` / `quant_mm/backtest.py`: historical V0.1-style market-making backtest.
- `gate_paper.py`: Gate public realtime paper market making; no real orders.
- `forecast.py`: multi-horizon forecast CLI.
- `dashboard.py`: detailed Streamlit forecast research page.
- `pages/01_Institutional_Alpha.py`: institutional-style alpha research page.
- `forecast_monitor.py`: long-running forecast maturity/verification monitor.
- `quant_mm/data.py`: exchange-native market-data transport, time semantics, retry/history guards.
- `quant_mm/market_cache.py`: persistent incremental OHLCV cache and data-quality metadata.
- `quant_mm/ui_labels.py`: presentation-only Chinese label/value translation.
- `quant_mm/direction_view.py`: pure next-candle direction/percentage/absolute-move presentation semantics.
- `quant_mm/forecast_engine.py`: next-candle ensemble forecast model, recency sample weights and bounded manual model multipliers.
- `quant_mm/forecast_store.py`: persistent forecast SQLite + maturity verification.
- `quant_mm/model_performance.py`: model-level realized leaderboard and dynamic weights.
- `quant_mm/institutional_alpha.py`: independent factor/derivatives alpha layer.
- `quant_mm/alpha_store.py`: alpha persistence + factor leaderboard.
- `quant_mm/gate_execution.py`: future Gate private execution skeleton; not a production live-trading engine.

## Tests to run

```bash
python -m pytest -q
```

When touching forecast time/data/model/UI semantics, specifically review:

- `tests/test_data_recency.py`
- `tests/test_forecast_time_alignment.py`
- `tests/test_forecast_store.py`
- `tests/test_gate_history_limit.py`
- `tests/test_gate_timeout_resilience.py`
- `tests/test_market_cache.py`
- `tests/test_forecast_custom_weights.py`
- `tests/test_forecast.py`
- `tests/test_institutional_alpha.py`
- `tests/test_model_performance.py`
- `tests/test_ui_labels.py`
- `tests/test_direction_view.py`
- `tests/test_auto_forecast.py`

## Documentation discipline

For meaningful architecture/data-semantics changes:

1. Update `docs/DEVELOPMENT_STATUS.md` or a focused handoff doc.
2. Update the relevant detailed doc under `docs/`.
3. Add a concise entry to `CHANGELOG.md`.
4. Add/adjust tests that encode the new invariant.

## Near-term roadmap

Priority order:

1. Stabilize data quality, time alignment, local cache, persistence, verification and operator UX.
2. Historical Funding/OI/Basis/Liquidation/Taker-flow datasets aligned to forecast candles.
3. Cross-asset BTC/ETH/SOL features and market breadth.
4. Alpha realized-performance weighting and correlation-aware portfolio of alphas.
5. HMM/regime models, LightGBM/XGBoost/quantile models after data quality is proven.
6. Gate TestNet order lifecycle/reconciliation only after research metrics are stable.

Do not jump directly to Transformer/deep-learning or live capital deployment before the above data/verification layers are mature.
