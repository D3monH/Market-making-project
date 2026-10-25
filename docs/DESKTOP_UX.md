# Desktop UX / Chinese UI Handoff

This document defines the preferred operator experience for the local Windows research platform.

## Goal

The normal user should not need to remember Python/Streamlit commands after initial setup.

Preferred flow:

```text
git pull
  -> run 创建桌面快捷方式.bat once
  -> desktop shortcut: Crypto量化研究平台
  -> double-click
  -> hidden local launcher
  -> background auto-forecast worker starts/reuses singleton
  -> browser opens http://127.0.0.1:8501
  -> Chinese navigation and research UI
```

The default page is the fast next-candle direction view. Daily use should answer the operator's core question first: will the next candle likely close up or down relative to the previous fully closed candle, and by roughly how much?

## Entry points

- `app.py` — preferred human-facing Streamlit entrypoint.
- `pages/00_Quick_Direction.py` — default fast next-candle direction page.
- `dashboard.py` — detailed multi-horizon forecast research page.
- `pages/01_Institutional_Alpha.py` — institutional Alpha research page.
- `start_dashboard.bat` — canonical Windows launcher and worker bootstrap.
- `auto_forecast_worker.py` — singleton background automatic-forecast process.
- `launch_dashboard_hidden.vbs` — hides the console when launched from the desktop shortcut.
- `create_desktop_shortcut.ps1` — creates `Crypto量化研究平台.lnk` on the current user's Desktop.
- `创建桌面快捷方式.bat` — one-click wrapper for the PowerShell shortcut installer.

## Quick-page information hierarchy

The quick page should not look like a research notebook. It should use a product-style hierarchy:

1. Large direction banner: `收涨（多） ↑` / `收跌（空） ↓`.
2. Primary cards: direction probability, expected return %, absolute quote-asset move, predicted close.
3. Secondary cards: previous close/model base, live reference price, model confidence, direction strength.
4. Always-visible model/data references: validation direction accuracy, model agreement, MAE, training samples and cache state.
5. Recent persisted prediction records on the same page.

Avoid packing many `st.metric()` widgets into narrow columns when values are likely to truncate. Prefer responsive HTML/CSS cards with readable wrapping and sensible price precision.

Deep model internals may remain on the detailed research page. The quick page should not force the user to open several expanders just to understand the current prediction.

## Quick direction semantics

The fast page must keep these meanings stable:

- `收涨（多） ↑`: predicted next close is above the previous fully closed candle close.
- `收跌（空） ↓`: predicted next close is below the previous fully closed candle close.
- The model base is always the previous fully closed candle close, never the live ticker.
- The primary return display is percentage return: `(predicted close - base close) / base close`.
- Absolute price change is secondary and is shown in the quote asset, e.g. `+182.5 USDT` for BTC/USDT. Do not present this as a universal "points return" because absolute price units are not comparable across BTC/ETH/SOL.
- Direction probability should correspond to the displayed direction: use `P(up)` for an up forecast and `1-P(up)` for a down forecast.
- If regression direction and the separate up/down probability model disagree, surface the disagreement instead of hiding it.
- A displayed direction is research output, not an order instruction.

## Prediction-record UX

The quick page must display recent records for the selected exchange/symbol/timeframe.

Duplicate semantics are strict:

```text
exchange + symbol + timeframe + base candle
```

may have only one official forecast. Repeated manual clicks must show the first stored forecast rather than recomputing a different value. The same rule applies to the automatic worker.

The history table should prioritize human-facing fields:

- target close time;
- status;
- predicted direction;
- predicted return %;
- predicted close;
- actual return %;
- actual close;
- direction hit/miss.

## Automatic forecast UX

The quick-page sidebar exposes persistent settings:

- enable/disable automatic forecasting;
- exchange;
- one or more symbols;
- one or more timeframes;
- polling interval;
- history window/cache preference inherited from the quick page.

Saving the setting writes `results/forecast/auto_forecast.json`. The desktop launcher starts the singleton worker, which rereads that file. The worker may check frequently, but it creates a new forecast only when the latest fully closed base candle changes.

The UI should make this explicit so users do not confuse “check every minute” with “predict a one-minute horizon”.

See `docs/AUTO_FORECAST.md` for worker semantics.

## Launcher behavior

`start_dashboard.bat` should:

1. Work from the repository directory regardless of the caller's current directory.
2. Create `.venv` automatically if it does not exist.
3. Prefer Python 3.11 through the Windows `py` launcher when available, otherwise fall back to the default Python.
4. Install `requirements.txt` only when core imports are unavailable.
5. Start `auto_forecast_worker.py` through `pythonw.exe` when available. The worker itself enforces singleton behavior.
6. Detect a healthy dashboard already listening on `127.0.0.1:8501`; if found, open it instead of starting another server.
7. Otherwise run `streamlit run app.py` on local port 8501.
8. Never require Gate API credentials for research pages.

### Windows PowerShell 5.1 encoding rule

`create_desktop_shortcut.ps1` must remain ASCII-only source code. Windows PowerShell 5.1 may interpret UTF-8 files without BOM using the legacy system code page; non-ASCII literals can become mojibake and can even break parsing. The Chinese shortcut name is therefore constructed at runtime from Unicode code points.

Do not add Chinese literals directly back into `create_desktop_shortcut.ps1`. Human-facing Chinese belongs in the Python/Streamlit presentation layer or can be generated at runtime when a legacy Windows script must stay encoding-safe.

## Chinese UI policy

Human-facing text is Chinese by default. Keep common industry names where translating them would reduce recognition, for example:

- BTC / ETH / SOL
- Alpha
- Funding
- OI / Open Interest
- Basis
- Gate / Binance / OKX / Bybit
- Linear / Ridge / ElasticNet / ExtraTrees

Raw internal identifiers must be translated for display rather than renamed in storage.

Examples:

| Internal | Display |
| --- | --- |
| `PENDING` | 等待收盘 |
| `SETTLED` | 已验真 |
| `gate_native_utc_day` | Gate 原生日K（北京时间08:00收盘） |
| `exchange_native_closed` | 交易所原生已收盘K |
| `legacy_alpha_unversioned` | 旧版 Alpha（未标记口径） |
| `timeframe` | 周期 |
| `composite_score` | Alpha 综合分 |
| `actual_return_pct` | 实际收益率(%) |
| Python `None` / missing table cells | `—` |

Presentation mapping lives in `quant_mm/ui_labels.py`.

## Why translation stays out of the database

SQLite/JSON/model contracts use stable English identifiers because they are consumed by tests, historical records, CLI code and future Codex changes. Changing database enums merely for display would create unnecessary migrations and could split historical statistics.

Therefore:

```text
stable internal identifier
        -> presentation translation
        -> Chinese UI
```

not:

```text
rename database schema/enums to Chinese
```

## Navigation

`app.py` uses `st.navigation` with explicit human-facing page names:

- `快速多空预测` — default daily-use page.
- `详细预测研究` — models, history, validation and charts.
- `机构 Alpha 研究` — derivatives/factor research.

Do not return to filename-derived labels such as `dashboard` or `Institutional Alpha` in the preferred launcher flow.

## Testing

`tests/test_ui_labels.py` protects Chinese presentation and launcher wiring.

`tests/test_direction_view.py` protects:

- up/down display semantics from predicted return sign;
- direction-specific probability display;
- quote-asset absolute price-change calculation;
- model-disagreement warnings.

`tests/test_auto_forecast.py` protects:

- saved settings normalization;
- duplicate target precheck before model training;
- generation of a new target when no record exists.

Always run:

```bash
python -m pytest -q
```

before merging UX/launcher changes.
