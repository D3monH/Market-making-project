from __future__ import annotations

"""Chinese desktop-style entrypoint for the Streamlit research UI.

The underlying research pages intentionally keep their internal identifiers in
English where they are part of data contracts. This entrypoint translates the
human-facing presentation while preserving stored values and model semantics.
"""

import pandas as pd
import streamlit as st


COLUMN_LABELS = {
    "timeframe": "周期",
    "status": "状态",
    "composite_score": "Alpha 综合分",
    "actual_return_pct": "实际收益率(%)",
    "candle_basis": "K线口径",
    "symbol": "币种",
    "exchange": "交易所",
    "samples": "样本数",
    "confidence": "置信度",
    "direction_correct": "方向命中",
    "target_close_at": "目标收盘时间",
    "forecast_run_at": "预测时间",
    "expected_return_pct": "预期收益率(%)",
    "expected_price": "预测收盘价",
}

VALUE_LABELS = {
    "PENDING": "等待收盘",
    "SETTLED": "已验真",
    "FAILED": "失败",
    "gate_native_utc_day": "Gate 原生日K（北京时间08:00收盘）",
    "exchange_native_closed": "交易所原生已收盘K",
    "legacy_alpha_unversioned": "旧版 Alpha（未标记口径）",
    "legacy_exchange_native": "旧版交易所原生K",
    "natural_day:Asia/Shanghai": "旧版北京时间自然日K（00:00收盘）",
    "cold_start": "首次建立缓存",
    "incremental": "增量更新",
    "gap_repair": "缺口修复",
    "offline_cache": "离线使用本地缓存",
    "network_only": "仅网络数据",
    "validation_only": "仅验证集自动权重",
    "validation_plus_realized": "验证集 + 真实战绩",
    "validation_plus_realized_plus_manual": "验证集 + 真实战绩 + 手动倍率",
    "validation_only_plus_manual": "验证集 + 手动倍率",
}

MODEL_LABELS = {
    "linear": "线性回归（Linear）",
    "ridge": "岭回归（Ridge）",
    "hist_gb": "梯度提升树（HistGB）",
    "elastic_net": "弹性网络（ElasticNet）",
    "extra_trees": "极端随机树（ExtraTrees）",
}

TEXT_REPLACEMENTS = {
    "Institutional Alpha Engine": "机构 Alpha 研究中心",
    "Alpha score": "Alpha 分数",
    "数据缓存：cold_start": "数据缓存：首次建立缓存",
    "数据缓存：incremental": "数据缓存：增量更新",
    "数据缓存：gap_repair": "数据缓存：缺口修复",
    "数据缓存：offline_cache": "数据缓存：离线使用本地缓存",
    "数据缓存：network_only": "数据缓存：仅网络数据",
    "K线口径：gate_native_utc_day": "K线口径：Gate 原生日K（北京时间08:00收盘）",
    "K线口径：exchange_native_closed": "K线口径：交易所原生已收盘K",
    "K线口径：legacy_alpha_unversioned": "K线口径：旧版 Alpha（未标记口径）",
}


def _translate_scalar(value):
    if isinstance(value, str):
        if value in VALUE_LABELS:
            return VALUE_LABELS[value]
        if value in MODEL_LABELS:
            return MODEL_LABELS[value]
    return value


def _translate_text(value):
    if not isinstance(value, str):
        return value
    if value in VALUE_LABELS:
        return VALUE_LABELS[value]
    if value in MODEL_LABELS:
        return MODEL_LABELS[value]
    out = value
    for source, target in TEXT_REPLACEMENTS.items():
        out = out.replace(source, target)
    for model_name, display_name in MODEL_LABELS.items():
        out = out.replace(f"{model_name} 倍率", f"{display_name} 倍率")
    return out


def _translate_dataframe(data):
    if not isinstance(data, pd.DataFrame):
        return data
    out = data.copy()
    out = out.rename(columns={name: COLUMN_LABELS.get(name, name) for name in out.columns})
    for column in out.columns:
        if out[column].dtype == object or pd.api.types.is_string_dtype(out[column]):
            out[column] = out[column].map(_translate_scalar)
    return out


# Keep the translation layer presentation-only. Stored data and model APIs keep
# their original stable identifiers.
_original_dataframe = st.dataframe
_original_title = st.title
_original_caption = st.caption
_original_markdown = st.markdown
_original_slider = st.slider
_original_bar_chart = st.bar_chart


def _dataframe(data=None, *args, **kwargs):
    return _original_dataframe(_translate_dataframe(data), *args, **kwargs)


def _title(body, *args, **kwargs):
    return _original_title(_translate_text(body), *args, **kwargs)


def _caption(body, *args, **kwargs):
    return _original_caption(_translate_text(body), *args, **kwargs)


def _markdown(body, *args, **kwargs):
    return _original_markdown(_translate_text(body), *args, **kwargs)


def _slider(label, *args, **kwargs):
    return _original_slider(_translate_text(label), *args, **kwargs)


def _bar_chart(data=None, *args, **kwargs):
    if "y_label" in kwargs:
        kwargs["y_label"] = _translate_text(kwargs["y_label"])
    return _original_bar_chart(_translate_dataframe(data), *args, **kwargs)


st.dataframe = _dataframe
st.title = _title
st.caption = _caption
st.markdown = _markdown
st.slider = _slider
st.bar_chart = _bar_chart

pages = [
    st.Page("dashboard.py", title="多周期预测", icon="📈", default=True),
    st.Page("pages/01_Institutional_Alpha.py", title="机构 Alpha 研究", icon="🧠"),
]

navigation = st.navigation(pages, position="sidebar")
navigation.run()
