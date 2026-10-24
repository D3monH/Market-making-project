from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from quant_mm.dashboard_utils import (
    build_price_projection,
    chart_in_display_timezone,
    forecast_window,
    format_display_timestamp,
    load_forecast_history,
    save_forecast_run,
)
from quant_mm.data import (
    fetch_closed_candle,
    fetch_last_price,
    forecast_candle_basis,
)
from quant_mm.forecast_engine import ForecastConfig, forecast_next_candle
from quant_mm.forecast_store import accuracy_summary, settle_due_forecasts, store_status
from quant_mm.market_cache import load_cached_forecast_ohlcv
from quant_mm.model_performance import model_performance_table, realized_model_weights


CONFIG_PATH = Path("config_forecast.yaml")


@st.cache_data(ttl=60, show_spinner=False)
def load_market_data(
    exchange: str,
    symbol: str,
    timeframe: str,
    bars: int,
    daily_timezone: str,
    use_cache: bool,
    cache_dir: str,
) -> pd.DataFrame:
    return load_cached_forecast_ohlcv(
        exchange,
        symbol,
        timeframe,
        bars,
        daily_timezone=daily_timezone,
        use_cache=use_cache,
        cache_dir=cache_dir,
    )


@st.cache_data(ttl=10, show_spinner=False)
def load_live_price(exchange: str, symbol: str) -> float:
    return fetch_last_price(exchange, symbol)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def fmt_pct(value: float, digits: int = 2) -> str:
    return f"{value:+.{digits}f}%"


def trend_cn(trend: str) -> str:
    return {
        "STRONG_UP": "强势上涨",
        "UP": "偏上涨",
        "SIDEWAYS": "震荡/中性",
        "DOWN": "偏下跌",
        "STRONG_DOWN": "强势下跌",
    }.get(trend, trend)


def signal_cn(signal: str) -> str:
    return {
        "LONG_BIAS": "偏多",
        "SHORT_BIAS": "偏空",
        "WAIT": "等待",
    }.get(signal, signal)


def regime_cn(regime: str) -> str:
    return {
        "TREND_UP": "上涨趋势",
        "TREND_DOWN": "下跌趋势",
        "RANGE": "震荡",
        "HIGH_VOL": "高波动",
    }.get(regime, regime)


def result_table(results: list[dict], display_timezone: str) -> pd.DataFrame:
    rows = []
    for row in results:
        window = forecast_window(row["as_of"], row["timeframe"])
        rows.append(
            {
                "币种": row["symbol"],
                "周期": row["timeframe"],
                "基准收盘时间": format_display_timestamp(
                    window["base_close_at"], display_timezone
                ),
                "目标收盘时间": format_display_timestamp(
                    window["target_close_at"], display_timezone
                ),
                "市场状态": regime_cn(row.get("market_regime", "")),
                "趋势": trend_cn(row["trend"]),
                "信号": signal_cn(row["research_signal"]),
                "预期收益": fmt_pct(row["expected_return_pct"], 3),
                "上涨概率": f"{row['up_probability']:.1%}",
                "置信度": f"{row['confidence']:.1%}",
                "方向验证准确率": f"{row['validation_direction_accuracy']:.1%}",
            }
        )
    return pd.DataFrame(rows)


def run_forecasts(
    exchange: str,
    symbol: str,
    timeframes: list[str],
    bars: int,
    cfg: ForecastConfig,
    results_dir: str,
    daily_timezone: str,
    use_cache: bool,
    cache_dir: str,
) -> tuple[list[dict], dict[str, pd.DataFrame], list[str]]:
    run_at = datetime.now(timezone.utc).isoformat()
    results: list[dict] = []
    market_data: dict[str, pd.DataFrame] = {}
    errors: list[str] = []

    for timeframe in timeframes:
        try:
            market = load_market_data(
                exchange,
                symbol,
                timeframe,
                bars,
                daily_timezone,
                use_cache,
                cache_dir,
            )
            candle_basis = forecast_candle_basis(
                exchange,
                timeframe,
                daily_timezone,
            )
            live_weights = realized_model_weights(
                results_dir,
                symbol=symbol,
                timeframe=timeframe,
                candle_basis=candle_basis,
                min_samples=cfg.realized_weight_min_samples,
            )
            forecast = forecast_next_candle(
                market,
                timeframe=timeframe,
                cfg=cfg,
                realized_model_weights=live_weights,
            )
            row = forecast.to_dict()
            row.update(
                {
                    "symbol": symbol,
                    "exchange": exchange,
                    "forecast_run_at": run_at,
                    "candle_basis": candle_basis,
                    "realized_model_weights": live_weights,
                }
            )
            results.append(row)
            market_data[timeframe] = market
        except Exception as exc:
            errors.append(f"{timeframe}: {exc}")

    if results:
        save_forecast_run(results, results_dir)
    return results, market_data, errors


def format_history(
    history: pd.DataFrame,
    display_timezone: str,
) -> pd.DataFrame:
    if history.empty:
        return history
    out = history.copy()
    out["状态"] = out.get("status", pd.Series(index=out.index, dtype=str)).map(
        {"PENDING": "待收盘", "SETTLED": "已验真"}
    ).fillna("未知")
    out["预测时间"] = out.get("forecast_run_at").map(
        lambda value: format_display_timestamp(value, display_timezone, include_seconds=True)
        if pd.notna(value)
        else "-"
    )
    out["周期"] = out.get("timeframe")
    out["K线口径"] = out.get("candle_basis", pd.Series(index=out.index, dtype=str)).fillna("-")
    out["趋势"] = out.get("trend", pd.Series(index=out.index, dtype=str)).map(trend_cn)
    out["预期收益%"] = pd.to_numeric(out.get("expected_return_pct"), errors="coerce")
    out["基准收盘时间"] = out.apply(
        lambda row: format_display_timestamp(
            forecast_window(row["as_of"], row["timeframe"])["base_close_at"],
            display_timezone,
        )
        if pd.notna(row.get("as_of")) and pd.notna(row.get("timeframe"))
        else "-",
        axis=1,
    )
    out["目标收盘时间"] = out.get("target_close_at").map(
        lambda value: format_display_timestamp(value, display_timezone)
        if pd.notna(value)
        else "-"
    )
    out["实际收益%"] = pd.to_numeric(out.get("actual_return_pct"), errors="coerce")
    out["方向命中"] = out.get("direction_correct").map(
        {1.0: "✅", 0.0: "❌", 1: "✅", 0: "❌"}
    )
    out["价格误差%"] = pd.to_numeric(out.get("price_abs_error_pct"), errors="coerce")
    out["区间命中"] = out.get("interval_hit").map(
        {1.0: "✅", 0.0: "❌", 1: "✅", 0: "❌"}
    )
    columns = [
        "预测时间",
        "周期",
        "K线口径",
        "状态",
        "基准收盘时间",
        "目标收盘时间",
        "趋势",
        "预期收益%",
        "实际收益%",
        "方向命中",
        "价格误差%",
        "区间命中",
    ]
    return out[columns]


st.set_page_config(
    page_title="Crypto Forecast Dashboard",
    page_icon="📈",
    layout="wide",
)

st.title("Crypto 多周期预测仪表盘")
st.caption(
    "预测只使用已经完整收盘的 K 线。实时价仅用于页面参考；模型权重可结合真实样本外战绩动态调整。"
)

config = load_config()
results_dir = config.get("results_dir", "results/forecast")
time_cfg = config.get("time", {}) or {}
daily_timezone = str(time_cfg.get("daily_candle_timezone", "UTC"))
display_timezone = str(time_cfg.get("display_timezone", "Asia/Shanghai"))
base_forecast_cfg = config.get("forecast", {}) or {}
market_data_cfg = config.get("market_data", {}) or {}
cache_dir = str(market_data_cfg.get("cache_dir", "data/cache/ohlcv"))

with st.sidebar:
    st.header("预测设置")

    exchange_options = ["gate", "binance", "okx", "bybit"]
    default_exchange = config.get("exchange", "gate")
    if default_exchange == "gateio":
        default_exchange = "gate"
    default_exchange_index = (
        exchange_options.index(default_exchange) if default_exchange in exchange_options else 0
    )
    exchange = st.selectbox("数据源交易所", exchange_options, index=default_exchange_index)

    preset_symbols = [
        "BTC/USDT",
        "ETH/USDT",
        "SOL/USDT",
        "DOGE/USDT",
        "XRP/USDT",
        "自定义",
    ]
    preset_symbol = st.selectbox("币种", preset_symbols, index=0)
    if preset_symbol == "自定义":
        symbol = st.text_input(
            "自定义交易对",
            value="BTC/USDT",
            placeholder="例如 ADA/USDT",
        ).strip().upper()
    else:
        symbol = preset_symbol

    timeframe_options = ["5m", "15m", "30m", "1h", "4h", "12h", "1d"]
    default_timeframes = [
        tf
        for tf in config.get("timeframes", ["15m", "1h", "4h", "1d"])
        if tf in timeframe_options
    ]
    timeframes = st.multiselect(
        "预测周期",
        timeframe_options,
        default=default_timeframes or ["15m", "1h"],
        help="每个周期独立训练；预测的是下一根该周期 K 线的收盘价。",
    )

    if exchange == "gate":
        st.caption(
            f"时间显示：`{display_timezone}`\n\n"
            "Gate 1d：交易所原生日 K（UTC 00:00→次日 00:00），"
            "北京时间显示为 **08:00→次日 08:00**。"
        )
    else:
        st.caption(
            f"时间显示：`{display_timezone}`\n\n"
            "1d 使用交易所原生日 K。"
        )

    display_mode = st.selectbox(
        "结果重点",
        ["综合预测", "趋势与概率", "价格目标与区间", "模型细节"],
        index=0,
    )

    bars = int(
        st.slider(
            "历史 K 线数量",
            min_value=1000,
            max_value=10000,
            value=int(config.get("bars", 5000)),
            step=500,
            help="这是本次模型最多使用的最近已收盘 K 线数量。本地缓存会长期增长，不会每次截成这个数量。",
        )
    )

    use_cache = st.checkbox(
        "启用本地 K 线缓存",
        value=bool(market_data_cfg.get("use_cache", True)),
        help="第一次下载完整窗口，之后只增量补最新 K 线；缓存保存在 data/cache/ohlcv。",
    )
    if use_cache:
        st.caption(f"本地缓存目录：`{cache_dir}`")

    with st.expander("高级模型参数"):
        train_window = int(
            st.number_input(
                "训练窗口",
                min_value=500,
                max_value=10000,
                value=int(base_forecast_cfg.get("train_window", 5000)),
                step=100,
            )
        )
        min_train_rows = int(
            st.number_input(
                "最少训练样本",
                min_value=300,
                max_value=5000,
                value=int(base_forecast_cfg.get("min_train_rows", 700)),
                step=50,
            )
        )
        neutral_threshold_bps = float(
            st.number_input(
                "中性阈值 bps",
                min_value=1.0,
                max_value=100.0,
                value=float(base_forecast_cfg.get("neutral_threshold_bps", 8.0)),
                step=1.0,
            )
        )
        strong_threshold_bps = float(
            st.number_input(
                "强趋势阈值 bps",
                min_value=2.0,
                max_value=300.0,
                value=float(base_forecast_cfg.get("strong_threshold_bps", 25.0)),
                step=1.0,
            )
        )
        use_advanced_models = st.checkbox(
            "启用高级模型（ElasticNet + ExtraTrees）",
            value=bool(base_forecast_cfg.get("use_advanced_models", True)),
        )
        realized_weight_blend = float(
            st.slider(
                "真实战绩权重占比",
                min_value=0.0,
                max_value=0.90,
                value=float(base_forecast_cfg.get("realized_weight_blend", 0.65)),
                step=0.05,
                help="达到最少验真样本后，真实战绩会与当前验证集权重混合。",
            )
        )
        realized_weight_min_samples = int(
            st.number_input(
                "真实战绩最少样本",
                min_value=10,
                max_value=500,
                value=int(base_forecast_cfg.get("realized_weight_min_samples", 30)),
                step=10,
            )
        )

        st.markdown("##### 训练样本时间权重")
        recency_enabled = st.checkbox(
            "让最近 K 线拥有更高训练权重",
            value=float(base_forecast_cfg.get("recency_half_life_bars", 0.0)) > 0.0,
        )
        default_half_life = int(max(100, float(base_forecast_cfg.get("recency_half_life_bars", 1500) or 1500)))
        recency_half_life_bars = float(
            st.number_input(
                "时间衰减 Half-Life（根）",
                min_value=100,
                max_value=10000,
                value=default_half_life,
                step=100,
                disabled=not recency_enabled,
                help="例如 1500：比最新样本早 1500 根的训练样本权重约为一半。",
            )
        ) if recency_enabled else 0.0

        st.markdown("##### 模型手动偏好倍率")
        manual_enabled = st.checkbox(
            "启用手动模型倍率",
            value=bool(base_forecast_cfg.get("manual_model_multipliers")),
            help="倍率会叠加在自动验证/真实战绩权重上，再重新归一化。范围限制为 0.25x~2.0x。",
        )
        configured_multipliers = base_forecast_cfg.get("manual_model_multipliers", {}) or {}
        model_multiplier_names = ["linear", "ridge", "hist_gb", "elastic_net", "extra_trees"]
        manual_model_multipliers: dict[str, float] = {}
        for model_name in model_multiplier_names:
            if model_name in {"elastic_net", "extra_trees"} and not use_advanced_models:
                continue
            multiplier = st.slider(
                f"{model_name} 倍率",
                min_value=0.25,
                max_value=2.0,
                value=float(configured_multipliers.get(model_name, 1.0)),
                step=0.05,
                disabled=not manual_enabled,
                key=f"manual_multiplier_{model_name}",
            )
            manual_model_multipliers[model_name] = float(multiplier if manual_enabled else 1.0)

    run_button = st.button("开始预测", type="primary", use_container_width=True)
    verify_button = st.button("立即验真到期预测", use_container_width=True)
    st.caption("页面打开时每 60 秒自动检查一次已经到期的预测。")

forecast_cfg = ForecastConfig(
    train_window=train_window,
    min_train_rows=min_train_rows,
    validation_fraction=float(base_forecast_cfg.get("validation_fraction", 0.20)),
    neutral_threshold_bps=neutral_threshold_bps,
    strong_threshold_bps=strong_threshold_bps,
    min_signal_confidence=float(base_forecast_cfg.get("min_signal_confidence", 0.52)),
    random_state=int(base_forecast_cfg.get("random_state", 42)),
    use_advanced_models=use_advanced_models,
    realized_weight_blend=realized_weight_blend,
    realized_weight_min_samples=realized_weight_min_samples,
    recency_half_life_bars=recency_half_life_bars,
    manual_model_multipliers=manual_model_multipliers if manual_enabled else {},
)

if "forecast_results" not in st.session_state:
    st.session_state.forecast_results = []
    st.session_state.market_data = {}
    st.session_state.forecast_errors = []
    st.session_state.forecast_symbol = None
    st.session_state.forecast_exchange = None

if verify_button:
    with st.spinner("正在检查已经收盘的预测..."):
        verification = settle_due_forecasts(
            results_dir,
            candle_fetcher=fetch_closed_candle,
            limit=200,
        )
    if verification["settled"]:
        st.sidebar.success(f"本次完成 {verification['settled']} 条验真")
    elif verification["errors"]:
        st.sidebar.warning("部分验真失败，请查看页面下方状态。")
    else:
        st.sidebar.info("当前没有新的到期预测。")

if run_button:
    if not symbol:
        st.error("请输入交易对，例如 BTC/USDT。")
    elif not timeframes:
        st.error("至少选择一个预测周期。")
    elif min_train_rows >= train_window:
        st.error("最少训练样本必须小于训练窗口。")
    elif strong_threshold_bps <= neutral_threshold_bps:
        st.error("强趋势阈值必须大于中性阈值。")
    else:
        with st.spinner(f"正在分析 {symbol}，请稍候..."):
            results, market_data, errors = run_forecasts(
                exchange=exchange,
                symbol=symbol,
                timeframes=timeframes,
                bars=bars,
                cfg=forecast_cfg,
                results_dir=results_dir,
                daily_timezone=daily_timezone,
                use_cache=use_cache,
                cache_dir=cache_dir,
            )
        st.session_state.forecast_results = results
        st.session_state.market_data = market_data
        st.session_state.forecast_errors = errors
        st.session_state.forecast_symbol = symbol
        st.session_state.forecast_exchange = exchange

results = st.session_state.forecast_results
market_data = st.session_state.market_data
errors = st.session_state.forecast_errors

if errors:
    with st.expander("部分周期预测失败", expanded=True):
        for error in errors:
            st.warning(error)

if not results:
    st.info("从左侧选择币种和周期，然后点击“开始预测”。")
else:
    current_symbol = st.session_state.forecast_symbol
    current_exchange = st.session_state.forecast_exchange or exchange
    st.subheader(f"{current_symbol} 预测总览")
    st.dataframe(
        result_table(results, display_timezone),
        use_container_width=True,
        hide_index=True,
    )

    try:
        live_price = load_live_price(current_exchange, current_symbol)
    except Exception:
        live_price = None

    tabs = st.tabs([row["timeframe"] for row in results])
    for tab, row in zip(tabs, results):
        with tab:
            window = forecast_window(row["as_of"], row["timeframe"])
            base_close_text = format_display_timestamp(
                window["base_close_at"], display_timezone
            )
            target_close_text = format_display_timestamp(
                window["target_close_at"], display_timezone
            )

            st.info(
                f"本次预测口径：使用 **{base_close_text} 已收盘价格** "
                f"`{row['current_price']:,.4f}` 作为模型基准，预测 "
                f"**{target_close_text} 收盘价**。实时价不会改变这条预测。"
            )
            if row["timeframe"] == "1d":
                if current_exchange == "gate":
                    st.caption(
                        "Gate 1d 使用交易所原生日 K：UTC 00:00→次日 00:00；"
                        "当前页面按北京时间显示，因此每天 **08:00 收盘**。"
                    )
                else:
                    st.caption("1d 使用当前交易所原生日 K。")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("趋势", trend_cn(row["trend"]))
            c2.metric("研究信号", signal_cn(row["research_signal"]))
            c3.metric("市场状态", regime_cn(row.get("market_regime", "")))
            c4.metric("上涨概率", f"{row['up_probability']:.1%}")

            c5, c6, c7, c8 = st.columns(4)
            c5.metric("上一根收盘 / 模型基准", f"{row['current_price']:,.4f}")
            c6.metric(
                "交易所实时价（仅参考）",
                f"{live_price:,.4f}" if live_price is not None else "获取失败",
            )
            c7.metric(
                "预测下一根收盘",
                f"{row['expected_price']:,.4f}",
                delta=fmt_pct(row["expected_return_pct"], 3),
            )
            c8.metric("置信度", f"{row['confidence']:.1%}")

            market = market_data.get(row["timeframe"])
            if market is not None:
                refresh_mode = market.attrs.get("cache_refresh_mode", "-")
                cache_rows = market.attrs.get("cache_rows_total", len(market))
                downloaded = market.attrs.get("downloaded_rows_this_run", 0)
                missing = market.attrs.get("missing_intervals", 0)
                cache_path = market.attrs.get("cache_path", "-")
                st.caption(
                    f"数据缓存：{refresh_mode} ｜ 本地累计 {cache_rows} 根 ｜ "
                    f"本次网络获取 {downloaded} 根 ｜ 缺口 {missing} ｜ `{cache_path}`"
                )
                if refresh_mode == "offline_cache":
                    st.warning("交易所刷新失败，本次使用仍满足时效要求的本地缓存。请留意网络状态。")

            weight_label = (
                "验证集 + 真实战绩"
                if "validation_plus_realized" in row.get("weighting_mode", "")
                else "仅时间序列验证集"
            )
            if "plus_manual" in row.get("weighting_mode", ""):
                weight_label += " + 手动倍率"
            recency_text = (
                f"Half-Life {row.get('recency_half_life_bars', 0):.0f} 根"
                if float(row.get("recency_half_life_bars", 0.0)) > 0.0
                else "关闭"
            )
            st.caption(
                f"历史方向验证准确率：{row['validation_direction_accuracy']:.1%} ｜ "
                f"模型一致度：{row['model_agreement']:.1%} ｜ "
                f"权重来源：{weight_label} ｜ 时间衰减：{recency_text} ｜ 训练样本：{row['training_rows']}"
            )

            if display_mode in {"综合预测", "价格目标与区间"}:
                st.markdown(
                    f"**预期收益：** `{fmt_pct(row['expected_return_pct'], 3)}`  "
                    f"｜ **预测区间：** `{row['lower_price']:,.4f}` ～ `{row['upper_price']:,.4f}`  "
                    f"｜ **近期波动率：** `{row['realized_volatility_pct']:.3f}%`  "
                    f"｜ **验证 MAE：** `{row['validation_mae_pct']:.3f}%`"
                )

            if market is not None and not market.empty:
                projection = build_price_projection(
                    market,
                    expected_price=row["expected_price"],
                    timeframe=row["timeframe"],
                    history_points=240,
                )
                projection = chart_in_display_timezone(
                    projection,
                    display_timezone,
                )
                st.line_chart(
                    projection,
                    height=360,
                    x_label=f"时间（{display_timezone}）",
                    y_label="价格",
                    alt=(
                        f"{current_symbol} {row['timeframe']} "
                        "历史收盘价与下一根预测收盘价"
                    ),
                )

            if display_mode in {"综合预测", "模型细节"}:
                st.markdown("#### 模型预测与最终权重")
                model_rows = []
                for model_name, pred_pct in row["model_predictions_pct"].items():
                    model_rows.append(
                        {
                            "模型": model_name,
                            "预测收益率(%)": pred_pct,
                            "验证集权重(%)": row.get("validation_model_weights", {}).get(model_name, 0.0) * 100.0,
                            "手动倍率": row.get("manual_model_multipliers", {}).get(model_name, 1.0),
                            "最终权重(%)": row["model_weights"].get(model_name, 0.0) * 100.0,
                        }
                    )
                model_df = pd.DataFrame(model_rows)
                left, right = st.columns([1, 1])
                with left:
                    st.dataframe(model_df, use_container_width=True, hide_index=True)
                with right:
                    st.bar_chart(
                        model_df.set_index("模型")[["最终权重(%)"]],
                        height=260,
                        y_label="权重 %",
                        alt="模型组合权重",
                    )

st.divider()
st.subheader("预测持久化、自动验真与模型排行榜")
st.caption(
    "每条预测都会保存到 SQLite。目标 K 线完整收盘后才验真，并用真实结果分别评估每个模型。"
)


@st.fragment(run_every="60s")
def verification_panel() -> None:
    verification = settle_due_forecasts(
        results_dir,
        candle_fetcher=fetch_closed_candle,
        limit=50,
    )
    status = store_status(results_dir)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("累计预测", status["total"])
    c2.metric("等待收盘", status["pending"])
    c3.metric("已验真", status["settled"])
    c4.metric("当前已到期未完成", status["due"])

    if status["next_target_close_at"]:
        next_close = format_display_timestamp(
            status["next_target_close_at"],
            display_timezone,
        )
        st.caption(f"下一条待验真的目标收盘时间：{next_close}（{display_timezone}）")
    st.caption(
        f"持久化数据库：`{status['db_path']}` ｜ 页面打开时每 60 秒自动检查"
    )

    if verification["errors"]:
        with st.expander("最近验真警告"):
            for error in verification["errors"]:
                st.warning(error)

    summary_symbol = st.session_state.forecast_symbol or symbol
    summary = accuracy_summary(results_dir, symbol=summary_symbol)
    if not summary.empty:
        shown = summary.copy()
        shown["方向命中率"] = shown["direction_accuracy"].map(
            lambda x: f"{x:.1%}" if pd.notna(x) else "-"
        )
        shown["有效信号命中率"] = shown["signal_accuracy"].map(
            lambda x: f"{x:.1%}" if pd.notna(x) else "-"
        )
        shown["平均价格误差"] = shown["price_mae_pct"].map(
            lambda x: f"{x:.3f}%" if pd.notna(x) else "-"
        )
        shown["收益预测 MAE"] = shown["return_mae_pct"].map(
            lambda x: f"{x:.3f}%" if pd.notna(x) else "-"
        )
        shown["预测区间命中率"] = shown["interval_hit_rate"].map(
            lambda x: f"{x:.1%}" if pd.notna(x) else "-"
        )
        shown = shown.rename(
            columns={
                "symbol": "币种",
                "timeframe": "周期",
                "candle_basis": "K线口径",
                "settled_count": "已验真次数",
                "directional_signal_count": "有效多空信号数",
            }
        )
        st.markdown(f"#### {summary_symbol} 真实验真统计")
        st.dataframe(
            shown[
                [
                    "币种",
                    "周期",
                    "K线口径",
                    "已验真次数",
                    "方向命中率",
                    "有效多空信号数",
                    "有效信号命中率",
                    "平均价格误差",
                    "收益预测 MAE",
                    "预测区间命中率",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

        leaderboard_timeframes = sorted(summary["timeframe"].dropna().unique().tolist())
        if leaderboard_timeframes:
            leaderboard_tf = st.selectbox(
                "模型排行榜周期",
                leaderboard_timeframes,
                key="model_leaderboard_timeframe",
            )
            leaderboard_basis = forecast_candle_basis(
                st.session_state.forecast_exchange or exchange,
                leaderboard_tf,
                daily_timezone,
            )
            leaderboard = model_performance_table(
                results_dir,
                symbol=summary_symbol,
                timeframe=leaderboard_tf,
                candle_basis=leaderboard_basis,
                limit=250,
            )
            if not leaderboard.empty:
                board = leaderboard.copy()
                board["方向命中率"] = board["direction_accuracy"].map(lambda x: f"{x:.1%}")
                board["收益 MAE"] = board["mae_pct"].map(lambda x: f"{x:.3f}%")
                board["真实战绩权重"] = board["realized_weight"].map(lambda x: f"{x:.1%}")
                board = board.rename(columns={"model": "模型", "samples": "验真样本"})
                st.markdown(f"#### {summary_symbol} / {leaderboard_tf} 模型真实战绩")
                st.dataframe(
                    board[["模型", "验真样本", "方向命中率", "收益 MAE", "真实战绩权重"]],
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.caption("该周期还没有足够的模型级真实验真数据。")

    history = load_forecast_history(results_dir, limit=500)
    if history.empty:
        st.caption("还没有历史预测记录。")
        return

    available_symbols = history.get("symbol", pd.Series(dtype=str)).dropna().unique().tolist()
    if not available_symbols:
        return
    selected = summary_symbol if summary_symbol in available_symbols else available_symbols[0]
    history_symbol = st.selectbox(
        "查看历史预测币种",
        available_symbols,
        index=available_symbols.index(selected),
        key="verification_history_symbol",
    )
    filtered = history[history["symbol"] == history_symbol].copy()
    st.dataframe(
        format_history(filtered.head(100), display_timezone),
        use_container_width=True,
        hide_index=True,
    )


verification_panel()

st.caption(
    "研究用途：模型预测存在误差，历史表现不保证未来收益。长期统计应重点看样本外命中率、误差、市场状态和模型稳定性。"
)
