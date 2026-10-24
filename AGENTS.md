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
5. New Gate 1d forecasts use Gate's exchange-native daily candle. Its boundary is UTC 00:00 -> next UTC 00:00, which displays as Beijing 08:00 -> next-day 08:00 in `Asia/Shanghai`. Do not rebuild new Gate daily forecasts as Beijing 00:00 -> 24:00.
6. Legacy records with `natural_day:Asia/Shanghai` are historical compatibility cohorts only. They may still be settled using the legacy 4h aggregation path, but must never be mixed with the new Gate-native daily cohort.
7. Verification happens only after the complete target candle has closed.
8. Realized model/alpha scores must come only from settled out-of-sample predictions.

## Market-data constraints

- All forecast market-data access should flow through `quant_mm/data.py`.
- Gate spot candlesticks reject excessively old `since/from` windows. The data layer contains a venue-aware history cap and safety margin. Do not bypass it by calling CCXT OHLCV directly from Streamlit pages.
- Gate public research instances intentionally set `exchange.has['fetchCurrencies'] = False` before `load_markets()`. The project only needs market-pair metadata for public ticker/OHLCV research, and `/spot/currencies` has caused avoidable startup timeouts. Do not re-enable it without a concrete requirement and regression coverage.
- Public CCXT network requests use bounded retries for transient `NetworkError` / `RequestTimeout` failures. Do not retry exchange parameter/validation errors as if they were network failures.
- A requested bar count is a preferred target. If a venue cannot supply that much history, use the maximum recent valid window and let the model's minimum-row checks decide whether training is possible.
- Do not reintroduce the old bug where pagination stops as soon as `bars` rows are collected before reaching current closed candles.
- `resample_daily_ohlcv()` exists for legacy-data compatibility; it is not the source for new Gate 1d forecasts.

## UI error-handling constraints

- Streamlit research pages should not expose a full traceback for routine market-data timeouts.
- A failed network request must not persist a new formal forecast/alpha record.
- Existing SQLite forecast/alpha history should remain readable even when current public APIs are temporarily unavailable.

## Current major components

- `main.py` / `quant_mm/backtest.py`: historical V0.1-style market-making backtest.
- `gate_paper.py`: Gate public realtime paper market making; no real orders.
- `forecast.py`: multi-horizon forecast CLI.
- `dashboard.py`: main Streamlit forecast dashboard.
- `pages/01_Institutional_Alpha.py`: institutional-style alpha research page.
- `forecast_monitor.py`: long-running forecast maturity/verification monitor.
- `quant_mm/forecast_engine.py`: next-candle ensemble forecast model.
- `quant_mm/forecast_store.py`: persistent forecast SQLite + maturity verification.
- `quant_mm/model_performance.py`: model-level realized leaderboard and dynamic weights.
- `quant_mm/institutional_alpha.py`: independent factor/derivatives alpha layer.
- `quant_mm/alpha_store.py`: alpha persistence + factor leaderboard.
- `quant_mm/gate_execution.py`: future Gate private execution skeleton; not a production live-trading engine.

## Tests to run

```bash
python -m pytest -q
```

When touching forecast time/data semantics, specifically review:

- `tests/test_data_recency.py`
- `tests/test_forecast_time_alignment.py`
- `tests/test_forecast_store.py`
- `tests/test_gate_history_limit.py`
- `tests/test_gate_timeout_resilience.py`
- `tests/test_institutional_alpha.py`
- `tests/test_model_performance.py`

## Documentation discipline

For meaningful architecture/data-semantics changes:

1. Update `docs/DEVELOPMENT_STATUS.md`.
2. Update the relevant detailed doc under `docs/`.
3. Add a concise entry to `CHANGELOG.md`.
4. Add/adjust tests that encode the new invariant.

## Near-term roadmap

Priority order:

1. Stabilize data quality, time alignment, persistence and verification.
2. Historical Funding/OI/Basis/Liquidation/Taker-flow datasets aligned to forecast candles.
3. Cross-asset BTC/ETH/SOL features and market breadth.
4. Alpha realized-performance weighting and correlation-aware portfolio of alphas.
5. HMM/regime models, LightGBM/XGBoost/quantile models after data quality is proven.
6. Gate TestNet order lifecycle/reconciliation only after research metrics are stable.

Do not jump directly to Transformer/deep-learning or live capital deployment before the above data/verification layers are mature.
