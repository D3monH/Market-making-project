from __future__ import annotations

"""Human-facing Chinese labels for the Streamlit UI.

Internal identifiers stay unchanged in storage/model contracts. This module is
presentation-only so data migrations and model semantics remain stable.
"""

import pandas as pd


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
    "model": "模型",
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
    "来源：price_only": "来源：仅价格因子",
    "来源：price_plus_derivatives": "来源：价格 + 衍生品因子",
}


def translate_scalar(value):
    if isinstance(value, str):
        if value in VALUE_LABELS:
            return VALUE_LABELS[value]
        if value in MODEL_LABELS:
            return MODEL_LABELS[value]
    return value


def translate_text(value):
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


def translate_dataframe(data):
    if not isinstance(data, pd.DataFrame):
        return data
    out = data.copy()
    out = out.rename(columns={name: COLUMN_LABELS.get(name, name) for name in out.columns})
    for column in out.columns:
        if out[column].dtype == object or pd.api.types.is_string_dtype(out[column]):
            out[column] = out[column].map(translate_scalar)
    return out
