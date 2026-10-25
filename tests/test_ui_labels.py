from __future__ import annotations

from pathlib import Path

import pandas as pd

from quant_mm.ui_labels import translate_dataframe, translate_text


def test_translate_internal_identifiers_to_chinese_labels():
    frame = pd.DataFrame(
        [
            {
                "timeframe": "1d",
                "status": "PENDING",
                "candle_basis": "gate_native_utc_day",
                "composite_score": -0.1,
                "actual_return_pct": None,
            }
        ]
    )
    shown = translate_dataframe(frame, fill_missing=True)

    assert list(shown.columns) == [
        "周期",
        "状态",
        "K线口径",
        "Alpha 综合分",
        "实际收益率(%)",
    ]
    assert shown.iloc[0]["状态"] == "等待收盘"
    assert shown.iloc[0]["K线口径"] == "Gate 原生日K（北京时间08:00收盘）"
    assert shown.iloc[0]["实际收益率(%)"] == "—"


def test_model_and_cache_labels_are_readable():
    assert translate_text("linear 倍率") == "线性回归（Linear） 倍率"
    assert translate_text("数据缓存：incremental") == "数据缓存：增量更新"
    assert translate_text("Institutional Alpha Engine") == "机构 Alpha 研究中心"


def test_one_click_launcher_is_wired_to_chinese_app_entrypoint():
    root = Path(__file__).resolve().parents[1]
    start = (root / "start_dashboard.bat").read_text(encoding="utf-8")
    shortcut = (root / "create_desktop_shortcut.ps1").read_text(encoding="utf-8")
    hidden = (root / "launch_dashboard_hidden.vbs").read_text(encoding="utf-8")

    assert "streamlit run app.py" in start
    assert "127.0.0.1:8501" in start
    assert "Crypto量化研究平台.lnk" in shortcut
    assert "launch_dashboard_hidden.vbs" in shortcut
    assert "start_dashboard.bat" in hidden
