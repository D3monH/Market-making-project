from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from quant_mm.dashboard_utils import (
    build_price_projection,
    load_forecast_history,
    save_forecast_run,
)
from quant_mm.data import fetch_ohlcv
from quant_mm.forecast_engine import ForecastConfig, forecast_next_candle


CONFIG_PATH = Path("config_forecast.yaml")


@st.cache_data(ttl=60, show_spinner=False)
def load_market_data(exchange: str, symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
    return fetch_ohlcv(exchange, symbol, timeframe, bars)


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


def result_table(results: list[dict]) -> pd.DataFrame:
    rows = []
    for row in results:
        rows.append(
            {
                "币种": row["symbol"],
                "周期": row["timeframe"],
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
) -> tuple[list[dict], dict[str, pd.DataFrame], list[str]]:
    run_at = datetime.now(timezone.utc).isoformat()
    results: list[dict] = []
    market_data: dict[str, pd.DataFrame] = {}
    errors: list[str] = []

    for timeframe in timeframes:
        try:
            market = load_market_data(exchange, symbol, timeframe, bars)
            forecast = forecast_next_candle(market, timeframe=timeframe, cfg=cfg)
            row = forecast.to_dict()
            row.update(
                {
                    "symbol": symbol,
                    "exchange": exchange,
                    "forecast_run_at": run_at,
                }
            )
            results.append(row)
            market_data[timeframe] = market
        except Exception as exc:
            errors.append(f"{timeframe}: {exc}")

    if results:
        save_forecast_run(results, results_dir)
    return results, market_data, errors


st.set_page_config(
    page_title="Crypto Forecast Dashboard",
    page_icon="📈",
    layout="wide",
)

st.title("Crypto 多周期预测仪表盘")
st.caption("选择币种与周期，一键运行多模型预测。当前只做研究预测，不会自动下单。")

config = load_config()
results_dir = config.get("results_dir", "results/forecast")

with st.sidebar:
    st.header("预测设置")

    exchange_options = ["gateio", "binance", "okx", "bybit"]
    default_exchange = config.get("exchange", "gateio")
    default_exchange_index = exchange_options.index(default_exchange) if default_exchange in exchange_options else 0
    exchange = st.selectbox("数据源交易所", exchange_options, index=default_exchange_index)

    preset_symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT", "XRP/USDT", "自定义"]
    preset_symbol = st.selectbox("币种", preset_symbols, index=0)
    if preset_symbol == "自定义":
        symbol = st.text_input("自定义交易对", value="BTC/USDT", placeholder="例如 ADA/USDT").strip().upper()
    else:
        symbol = preset_symbol

    timeframe_options = ["5m", "15m", "30m", "1h", "4h", "12h", "1d"]
    default_timeframes = [tf for tf in config.get("timeframes", ["15m", "1h", "4h", "1d"]) if tf in timeframe_options]
    timeframes = st.multiselect(
        "预测周期",
        timeframe_options,
        default=default_timeframes or ["15m", "1h"],
        help="每个周期都会独立训练并预测下一根该周期 K 线。",
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
            help="更多历史数据可能更稳定，但首次运行会更慢。",
        )
    )

    with st.expander("高级模型参数"):
        base_forecast_cfg = config.get("forecast", {})
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

    run_button = st.button("开始预测", type="primary", use_container_width=True)
    st.caption("提示：首次预测需要下载历史 K 线并训练模型。")

forecast_cfg = ForecastConfig(
    train_window=train_window,
    min_train_rows=min_train_rows,
    validation_fraction=float(config.get("forecast", {}).get("validation_fraction", 0.20)),
    neutral_threshold_bps=neutral_threshold_bps,
    strong_threshold_bps=strong_threshold_bps,
    min_signal_confidence=float(config.get("forecast", {}).get("min_signal_confidence", 0.52)),
    random_state=int(config.get("forecast", {}).get("random_state", 42)),
)

if "forecast_results" not in st.session_state:
    st.session_state.forecast_results = []
    st.session_state.market_data = {}
    st.session_state.forecast_errors = []
    st.session_state.forecast_symbol = None

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
            )
        st.session_state.forecast_results = results
        st.session_state.market_data = market_data
        st.session_state.forecast_errors = errors
        st.session_state.forecast_symbol = symbol

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
    st.subheader(f"{current_symbol} 预测总览")
    st.dataframe(result_table(results), use_container_width=True, hide_index=True)

    tabs = st.tabs([row["timeframe"] for row in results])
    for tab, row in zip(tabs, results):
        with tab:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("趋势", trend_cn(row["trend"]))
            c2.metric("研究信号", signal_cn(row["research_signal"]))
            c3.metric("预期收益", fmt_pct(row["expected_return_pct"], 3))
            c4.metric("上涨概率", f"{row['up_probability']:.1%}")

            c5, c6, c7, c8 = st.columns(4)
            c5.metric("当前价", f"{row['current_price']:,.4f}")
            c6.metric(
                "预测下一根收盘",
                f"{row['expected_price']:,.4f}",
                delta=fmt_pct(row["expected_return_pct"], 3),
            )
            c7.metric("置信度", f"{row['confidence']:.1%}")
            c8.metric("历史方向准确率", f"{row['validation_direction_accuracy']:.1%}")

            if display_mode in {"综合预测", "价格目标与区间"}:
                st.markdown(
                    f"**预测区间：** `{row['lower_price']:,.4f}` ～ `{row['upper_price']:,.4f}`  "
                    f"｜ **近期波动率：** `{row['realized_volatility_pct']:.3f}%`  "
                    f"｜ **验证 MAE：** `{row['validation_mae_pct']:.3f}%`"
                )

            market = market_data.get(row["timeframe"])
            if market is not None and not market.empty:
                projection = build_price_projection(
                    market,
                    expected_price=row["expected_price"],
                    timeframe=row["timeframe"],
                    history_points=240,
                )
                st.line_chart(
                    projection,
                    height=360,
                    x_label="时间",
                    y_label="价格",
                    alt=f"{current_symbol} {row['timeframe']} 历史收盘价与下一根预测收盘价",
                )

            if display_mode in {"综合预测", "模型细节"}:
                st.markdown("#### 模型预测与权重")
                model_rows = []
                for model_name, pred_pct in row["model_predictions_pct"].items():
                    model_rows.append(
                        {
                            "模型": model_name,
                            "预测收益率(%)": pred_pct,
                            "组合权重(%)": row["model_weights"].get(model_name, 0.0) * 100.0,
                        }
                    )
                model_df = pd.DataFrame(model_rows)
                left, right = st.columns([1, 1])
                with left:
                    st.dataframe(model_df, use_container_width=True, hide_index=True)
                with right:
                    st.bar_chart(
                        model_df.set_index("模型")[["组合权重(%)"]],
                        height=260,
                        y_label="权重 %",
                        alt="模型组合权重",
                    )
                st.caption(
                    f"模型一致度：{row['model_agreement']:.1%} ｜ 训练样本：{row['training_rows']}"
                )

    st.divider()
    st.subheader("历史预测记录")
    history = load_forecast_history(results_dir, limit=300)
    if history.empty:
        st.caption("还没有历史记录。")
    else:
        available_symbols = history.get("symbol", pd.Series(dtype=str)).dropna().unique().tolist()
        history_symbol = st.selectbox(
            "查看历史币种",
            available_symbols,
            index=available_symbols.index(current_symbol) if current_symbol in available_symbols else 0,
            key="history_symbol",
        )
        filtered = history[history["symbol"] == history_symbol].copy()
        wanted = [
            c
            for c in [
                "forecast_run_at",
                "timeframe",
                "trend",
                "research_signal",
                "expected_return_pct",
                "up_probability",
                "confidence",
                "validation_direction_accuracy",
            ]
            if c in filtered.columns
        ]
        st.dataframe(filtered[wanted].tail(100), use_container_width=True, hide_index=True)

st.caption("研究用途：模型预测存在误差，历史表现不保证未来收益。实盘接入前应先做长期 Paper/TestNet 验证。")
