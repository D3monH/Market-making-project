# Auto Forecast / Background Worker Handoff

This document defines the persistent automatic-forecast behavior used by the Windows desktop launcher.

## Goal

The user may configure automatic forecasting from the `快速多空预测` page and then rely on the desktop shortcut to keep a lightweight background worker running.

The worker must never create multiple formal forecasts for the same target candle.

## Files

- `quant_mm/auto_forecast.py`
  - persistent JSON settings/status helpers;
  - existing-forecast lookup;
  - one automatic forecast cycle.
- `auto_forecast_worker.py`
  - long-running singleton background process;
  - rereads saved settings;
  - triggers forecast cycles at the configured polling interval.
- `start_dashboard.bat`
  - starts the worker with `pythonw.exe` before opening/reusing the Streamlit dashboard.

## Persistent settings

Default path:

```text
results/forecast/auto_forecast.json
```

Fields:

```json
{
  "enabled": false,
  "exchange": "gate",
  "symbols": ["BTC/USDT"],
  "timeframes": ["15m"],
  "bars": 5000,
  "use_cache": true,
  "poll_seconds": 60
}
```

The status file is:

```text
results/forecast/auto_forecast_status.json
```

It is presentation/operations metadata only; forecast truth remains in `forecast_history.sqlite3`.

## Duplicate invariant

Every cycle first refreshes/loads the latest fully closed candle. Before any model training, it checks whether the database already contains:

```text
exchange + symbol + timeframe + as_of(base candle open)
```

If found, the cycle skips training for that target.

The SQLite unique index remains the final database-level guard. The pre-training check exists to avoid wasting CPU and, more importantly, to prevent the UI from showing a recomputed value that differs from the first official prediction.

Manual quick-page behavior follows the same rule: repeated clicks on the same target candle must load and display the first stored prediction instead of recomputing it.

## Polling semantics

`poll_seconds` is only how often the worker checks for a new closed candle. It is not the forecast horizon.

Example:

```text
BTC/USDT 15m
poll every 1 minute
```

The worker may wake 15 times during the same target window, but it records only one forecast because the base candle has not changed. After the next 15m candle closes, the base `as_of` changes and one new forecast becomes eligible.

## Worker singleton

`auto_forecast_worker.py` binds local TCP port `127.0.0.1:8766` only as a singleton lock. A second worker process exits immediately if that port is already bound.

The port is not an HTTP API and should not be exposed externally.

## Verification

Each automatic cycle may also settle due forecast records using the existing `settle_due_forecasts()` logic. Verification still happens only after the complete target candle has closed.

## Safety / scope

Automatic forecasting is research automation only. It does not place orders and does not require exchange private API credentials.

Do not connect this worker directly to live-order execution without a separate explicit execution/risk design.
