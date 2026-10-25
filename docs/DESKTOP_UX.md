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
  -> browser opens http://127.0.0.1:8501
  -> Chinese navigation and research UI
```

## Entry points

- `app.py` — preferred human-facing Streamlit entrypoint.
- `dashboard.py` — multi-horizon forecast research page.
- `pages/01_Institutional_Alpha.py` — institutional Alpha research page.
- `start_dashboard.bat` — canonical Windows launcher.
- `launch_dashboard_hidden.vbs` — hides the console when launched from the desktop shortcut.
- `create_desktop_shortcut.ps1` — creates `Crypto量化研究平台.lnk` on the current user's Desktop.
- `创建桌面快捷方式.bat` — one-click wrapper for the PowerShell shortcut installer.

## Launcher behavior

`start_dashboard.bat` should:

1. Work from the repository directory regardless of the caller's current directory.
2. Detect a healthy dashboard already listening on `127.0.0.1:8501`; if found, open it instead of starting another server.
3. Create `.venv` automatically if it does not exist.
4. Prefer Python 3.11 through the Windows `py` launcher when available, otherwise fall back to the default Python.
5. Install `requirements.txt` only when core imports are unavailable.
6. Run `streamlit run app.py` on local port 8501.
7. Never require Gate API credentials for research pages.

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

- `多周期预测`
- `机构 Alpha 研究`

Do not return to filename-derived labels such as `dashboard` or `Institutional Alpha` in the preferred launcher flow.

## Testing

`tests/test_ui_labels.py` protects:

- Chinese status/candle-basis/table-column translation.
- missing-value rendering policy.
- model/cache label translation.
- launcher wiring to `app.py` and the desktop shortcut chain.

Always run:

```bash
python -m pytest -q
```

before merging UX/launcher changes.
