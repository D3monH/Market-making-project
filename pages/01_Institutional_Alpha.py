from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from quant_mm.alpha_store import (
    alpha_history,
    alpha_summary,
    factor_leaderboard,
    persist_alpha,
    settle_due_alpha,
)
from quant_mm.dashboard_utils import forecast_window, format_display_timestamp, save_forecast_run
from quant_mm.data import (
    fetch_closed_candle,
    fetch_last_price,
    forecast_candle_basis,
)
from quant_mm.forecast_engine import ForecastConfig, forecast_next_candle
from quant_mm.forecast_store import settle_due_forecasts
from quant_mm.institutional_alpha import (
    build_institutional_alpha,
    fetch_gate_derivatives_context,
)
from quant_mm.market_cache import load_cached_forecast_ohlcv
from quant_mm.model_performance import realized_model_weights

CONFIG_PATH = Path("config_forecast.yaml")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def signal_cn(value: str) -> str:
    return {
        "STRONG_LONG_BIAS": "强偏多",
        "LONG_BIAS": "偏多",
        "WAIT": "等待",
        "SHORT_BIAS": "偏空",
        "STRONG_SHORT_BIAS": "强偏空",
    }.get(value, value)


def regime_cn(value: str) -> str:
    return {
        "TREND_UP": "上涨趋势",
        "TREND_DOWN": "下跌趋势",
        "RANGE": "震荡",
        "HIGH_VOL": "高波动",
    }.get(value, value)


def factor_cn(value: str) -> str:
    return {
        "momentum": "动量",
        "trend": "趋势",
        "mean_reversion": "均值回归",
        "carry_crowding": "Funding / Carry 拥挤",
        "open_interest": "Open Interest",
        "basis_crowding": "合约溢价 / Basis",
        "positioning": "多空持仓拥挤",
        "microstructure": "盘口不平衡",
    }.get(value, value)


def friendly_market_error(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    if "network attempts" in lowered or "timeout" in lowered or "timed out" in lowered:
        return (
            "Gate 公共行情请求超时。程序已经自动重试，但当前网络仍未成功。"
            "如果本地缓存仍满足时效要求会自动回退使用；否则稍后再次点击即可。"
        )
    if "stale" in lowered:
        return "本地行情缓存已经过期且交易所刷新失败，已拒绝生成正式预测。请恢复网络后重试。"
    return f"行情/预测执行失败：{text}"


@st.cache_data(ttl=60, show_spinner=False)
def load_market(
    exchange: str,
    symbol: str,
    timeframe: str,
    bars: int,
    daily_timezone: str,
    use_cache: bool,
    cache_dir: str,
):
    return load_cached_forecast_ohlcv(
        exchange,
        symbol,
        timeframe,
        bars,
        daily_timezone=daily_timezone,
        use_cache=use_cache,
        cache_dir=cache_dir,
    )


@st.cache_data(ttl=30, show_spinner=False)
def load_derivatives(symbol: str, timeframe: str):
    return fetch_gate_derivatives_context(symbol, timeframe)


@st.cache_data(ttl=10, show_spinner=False)
def load_live_price(exchange: str, symbol: str) -> float:
    return fetch_last_price(exchange, symbol)


st.set_page_config(page_title="Institutional Alpha Engine", page_icon="🧠", layout="wide")
st.title("Institutional Alpha Engine")
st.caption(
    "机构化研究层：价格模型负责预测下一根收盘；Alpha 层用趋势、均值回归、Funding、OI、Basis、持仓拥挤和盘口信息做独立确认与风险控制。"
)

cfg = load_config()
results_dir = str(cfg.get("results_dir", "results/forecast"))
time_cfg = cfg.get("time", {}) or {}
daily_timezone = str(time_cfg.get("daily_candle_timezone", "UTC"))
display_timezone = str(time_cfg.get("display_timezone", "Asia/Shanghai"))
forecast_raw = cfg.get("forecast", {}) or {}
alpha_cfg = cfg.get("institutional_alpha", {}) or {}
market_data_cfg = cfg.get("market_data", {}) or {}
cache_dir = str(market_data_cfg.get("cache_dir", "data/cache/ohlcv"))

with st.sidebar:
    st.header("机构 Alpha 设置")
    exchange = st.selectbox("数据源", ["gate", "binance", "okx", "bybit"], index=0)
    symbol = st.selectbox(
        "币种",
        ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT", "XRP/USDT"],
        index=0,
    )
    timeframe = st.selectbox("周期", ["5m", "15m", "30m", "1h", "4h", "12h", "1d"], index=1)
    bars = int(
        st.slider(
            "历史 K 线",
            min_value=1000,
            max_value=10000,
            value=int(cfg.get("bars", 5000)),
            step=500,
            help="训练使用最近窗口；本地缓存会长期增长，后续运行只增量同步。",
        )
    )
    use_cache = st.checkbox(
        "启用本地 K 线缓存",
        value=bool(market_data_cfg.get("use_cache", True)),
        help="第一次完整下载，后续只补最新 K 线。",
    )
    if use_cache:
        st.caption(f"缓存目录：`{cache_dir}`")
    use_derivatives = st.checkbox(
        "启用 Gate 公共衍生品 Alpha",
        value=bool(alpha_cfg.get("use_gate_derivatives", True)),
        help="读取公开 Funding、Open Interest、合约溢价、多空比和盘口数据；不需要 API Key。",
    )
    if exchange == "gate" and timeframe == "1d":
        st.caption(
            "Gate 1d 使用交易所原生日 K：UTC 00:00→次日 00:00；"
            "北京时间显示为 **08:00→次日 08:00**。"
        )
    else:
        st.caption("非 Gate 数据源时，价格模型仍可运行；Gate 衍生品 Alpha 仅在 Gate 上启用。")
    run = st.button("运行机构版预测", type="primary", use_container_width=True)

forecast_cfg = ForecastConfig(**forecast_raw)

if "institutional_result" not in st.session_state:
    st.session_state.institutional_result = None
    st.session_state.institutional_market = None
    st.session_state.institutional_error = None

if run:
    st.session_state.institutional_error = None
    try:
        with st.spinner(f"正在构建 {symbol} / {timeframe} 机构 Alpha..."):
            market = load_market(
                exchange,
                symbol,
                timeframe,
                bars,
                daily_timezone,
                use_cache,
                cache_dir,
            )
            basis = forecast_candle_basis(exchange, timeframe, daily_timezone)
            realized = realized_model_weights(
                results_dir,
                symbol=symbol,
                timeframe=timeframe,
                candle_basis=basis,
                min_samples=forecast_cfg.realized_weight_min_samples,
            )
            forecast = forecast_next_candle(
                market,
                timeframe=timeframe,
                cfg=forecast_cfg,
                realized_model_weights=realized,
            )
            derivatives = None
            if exchange == "gate" and use_derivatives:
                derivatives = load_derivatives(symbol, timeframe)
            alpha = build_institutional_alpha(
                market,
                expected_return_pct=forecast.expected_return_pct,
                model_confidence=forecast.confidence,
                derivatives_context=derivatives,
                market_regime=forecast.market_regime,
            )

            row = forecast.to_dict()
            row.update(
                {
                    "symbol": symbol,
                    "exchange": exchange,
                    "forecast_run_at": datetime.now(timezone.utc).isoformat(),
                    "candle_basis": basis,
                    "realized_model_weights": realized,
                    "institutional_alpha": alpha.to_dict(),
                }
            )
            save_forecast_run([row], results_dir)
            persist_alpha(
                results_dir,
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                as_of=forecast.as_of,
                base_price=forecast.current_price,
                model_expected_return_pct=forecast.expected_return_pct,
                model_confidence=forecast.confidence,
                alpha=alpha.to_dict(),
                candle_basis=basis,
                forecast_run_at=row["forecast_run_at"],
            )
            st.session_state.institutional_result = row
            st.session_state.institutional_market = market
    except Exception as exc:
        st.session_state.institutional_error = friendly_market_error(exc)

if st.session_state.institutional_error:
    st.error(st.session_state.institutional_error)
    st.caption(
        "网络问题不会写入一条新的正式预测。已有 SQLite 历史仍保留；"
        "如果使用代理/VPN，也请确认 Python 进程能够访问 Gate 公共 API。"
    )

row = st.session_state.institutional_result
market = st.session_state.institutional_market

if row is None:
    st.info("选择币种和周期后点击“运行机构版预测”。")
else:
    alpha = row["institutional_alpha"]
    window = forecast_window(row["as_of"], row["timeframe"])
    base_close = format_display_timestamp(window["base_close_at"], display_timezone)
    target_close = format_display_timestamp(window["target_close_at"], display_timezone)
    try:
        live_price = load_live_price(row["exchange"], row["symbol"])
    except Exception:
        live_price = None

    st.subheader(f"{row['symbol']} / {row['timeframe']} 机构共识")
    st.info(
        f"模型基准使用 **{base_close} 已收盘价格 {row['current_price']:,.4f}**，"
        f"目标是 **{target_close} 收盘价**。实时价只做参考。"
    )
    if row["timeframe"] == "1d" and row["exchange"] == "gate":
        st.caption(
            "Gate 原生 1d 按 UTC 日界线；当前页面显示北京时间，因此上一根/目标日线都在 **08:00** 收盘。"
        )

    if market is not None:
        st.caption(
            f"数据缓存：{market.attrs.get('cache_refresh_mode', '-')} ｜ "
            f"本地累计 {market.attrs.get('cache_rows_total', len(market))} 根 ｜ "
            f"本次网络获取 {market.attrs.get('downloaded_rows_this_run', 0)} 根 ｜ "
            f"缺口 {market.attrs.get('missing_intervals', 0)} ｜ "
            f"`{market.attrs.get('cache_path', '-')}`"
        )
        if market.attrs.get("cache_refresh_mode") == "offline_cache":
            st.warning("交易所刷新失败，本次使用仍满足时效要求的本地缓存。")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("模型预测下一收盘", f"{row['expected_price']:,.4f}", delta=f"{row['expected_return_pct']:+.3f}%")
    c2.metric("机构共识信号", signal_cn(alpha["consensus_signal"]))
    c3.metric("机构共识置信度", f"{alpha['consensus_confidence']:.1%}")
    c4.metric("市场状态", regime_cn(row.get("market_regime", "")))

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("模型置信度", f"{row['confidence']:.1%}")
    c6.metric("Alpha 综合分", f"{alpha['composite_score']:+.3f}")
    c7.metric("风险倍率", f"{alpha['risk_multiplier']:.2f}x")
    c8.metric("实时价（仅参考）", f"{live_price:,.4f}" if live_price is not None else "获取失败")

    st.caption(
        f"K线口径：{row.get('candle_basis', '-')} ｜ "
        f"Alpha 数据完整度：{alpha['data_quality']:.1%} ｜ 来源：{alpha['source']} ｜ "
        "机构共识不会回写修改模型的预测收盘价，避免尚未验真的衍生品因子污染价格模型。"
    )

    scores = alpha.get("factor_scores", {})
    weights = alpha.get("factor_weights", {})
    factor_rows = [
        {
            "因子": factor_cn(name),
            "方向分数 (-1~+1)": float(score),
            "当前组合权重": float(weights.get(name, 0.0)),
            "解释": "偏多" if score > 0.12 else "偏空" if score < -0.12 else "中性",
        }
        for name, score in scores.items()
    ]
    factor_df = pd.DataFrame(factor_rows)
    st.markdown("### Alpha 因子拆解")
    left, right = st.columns([1.15, 0.85])
    with left:
        st.dataframe(factor_df, use_container_width=True, hide_index=True)
    with right:
        if not factor_df.empty:
            st.bar_chart(
                factor_df.set_index("因子")[["方向分数 (-1~+1)"]],
                height=330,
                y_label="Alpha score",
            )

    raw = alpha.get("raw_context", {})
    derivatives = raw.get("derivatives", {}) or {}
    if derivatives:
        st.markdown("### Gate 衍生品上下文")
        raw_rows = []
        labels = {
            "funding_rate": "Funding Rate",
            "funding_z": "Funding Z-Score",
            "open_interest_usd": "Open Interest (USD)",
            "open_interest_change": "OI 近期变化",
            "perp_premium": "Perp / Index 溢价",
            "long_short_account_ratio": "多空账户比",
            "top_book_imbalance": "Top Book Imbalance",
            "futures_last": "合约最新价",
            "index_price": "指数价",
            "mark_price": "标记价",
        }
        for key, value in derivatives.items():
            raw_rows.append({"指标": labels.get(key, key), "数值": value})
        st.dataframe(pd.DataFrame(raw_rows), use_container_width=True, hide_index=True)

    if raw.get("derivatives_errors"):
        with st.expander("衍生品数据警告"):
            for error in raw["derivatives_errors"]:
                st.warning(error)

st.divider()
st.subheader("Alpha 长期自动验真")
st.caption("每个 Alpha 因子都会保留当时的方向分数。目标 K 线收盘后，系统自动统计共识和单因子的真实表现。")


@st.fragment(run_every="60s")
def alpha_verification_panel():
    settle_due_forecasts(results_dir, candle_fetcher=fetch_closed_candle, limit=50)
    result = settle_due_alpha(results_dir, candle_fetcher=fetch_closed_candle, limit=50)
    if result["errors"]:
        with st.expander("Alpha 验真警告"):
            for error in result["errors"]:
                st.warning(error)

    summary = alpha_summary(results_dir, symbol=symbol)
    if not summary.empty:
        shown = summary.copy()
        shown["方向命中率"] = shown["direction_accuracy"].map(lambda x: f"{x:.1%}" if pd.notna(x) else "-")
        shown["有效信号命中率"] = shown["signal_accuracy"].map(lambda x: f"{x:.1%}" if pd.notna(x) else "-")
        shown["平均置信度"] = shown["avg_confidence"].map(lambda x: f"{x:.1%}" if pd.notna(x) else "-")
        shown["数据完整度"] = shown["avg_data_quality"].map(lambda x: f"{x:.1%}" if pd.notna(x) else "-")
        shown = shown.rename(
            columns={
                "symbol": "币种",
                "timeframe": "周期",
                "candle_basis": "K线口径",
                "samples": "样本",
            }
        )
        st.dataframe(
            shown[["币种", "周期", "K线口径", "样本", "方向命中率", "有效信号命中率", "平均置信度", "数据完整度"]],
            use_container_width=True,
            hide_index=True,
        )

    current_basis = forecast_candle_basis(exchange, timeframe, daily_timezone)
    board = factor_leaderboard(
        results_dir,
        symbol=symbol,
        timeframe=timeframe,
        candle_basis=current_basis,
        limit=500,
    )
    if not board.empty:
        display = board.copy()
        display["方向命中率"] = display["direction_accuracy"].map(lambda x: f"{x:.1%}")
        display["因子"] = display["factor"].map(factor_cn)
        display = display.rename(columns={"samples": "样本", "avg_signed_edge": "平均有符号收益贡献"})
        st.markdown(f"#### {symbol} / {timeframe} Alpha 因子真实排行榜")
        st.caption(f"当前排行榜只统计 K 线口径：`{current_basis}`")
        st.dataframe(
            display[["因子", "样本", "方向命中率", "平均有符号收益贡献"]],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("当前口径还没有足够已验真的 Alpha 历史。继续运行后这里会形成因子排行榜。")

    history = alpha_history(results_dir, limit=100)
    if not history.empty:
        shown = history[history["symbol"] == symbol].copy()
        if not shown.empty:
            shown["目标收盘时间"] = shown["target_close_at"].map(
                lambda x: format_display_timestamp(x, display_timezone)
            )
            shown["共识信号"] = shown["consensus_signal"].map(signal_cn)
            shown["方向命中"] = shown["direction_correct"].map({1.0: "✅", 0.0: "❌", 1: "✅", 0: "❌"})
            shown["K线口径"] = shown["candle_basis"].fillna("legacy_alpha_unversioned")
            st.markdown("#### 最近 Alpha 记录")
            st.dataframe(
                shown[["timeframe", "K线口径", "目标收盘时间", "status", "composite_score", "共识信号", "actual_return_pct", "方向命中"]].head(50),
                use_container_width=True,
                hide_index=True,
            )


alpha_verification_panel()

st.caption(
    "研究用途。该页面采用机构式“独立 Alpha + 共识 + 风控”结构；只有经过持续样本外验真的因子，才适合进一步进入自动交易决策。"
)
