from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
import yaml

from quant_mm.dashboard_utils import forecast_window, format_display_timestamp, save_forecast_run
from quant_mm.data import fetch_last_price, forecast_candle_basis
from quant_mm.direction_view import build_direction_view
from quant_mm.forecast_engine import ForecastConfig, forecast_next_candle
from quant_mm.market_cache import load_cached_forecast_ohlcv
from quant_mm.model_performance import realized_model_weights


CONFIG_PATH = Path("config_forecast.yaml")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


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


@st.cache_data(ttl=10, show_spinner=False)
def load_live_price(exchange: str, symbol: str) -> float:
    return fetch_last_price(exchange, symbol)


cfg = load_config()
results_dir = str(cfg.get("results_dir", "results/forecast"))
time_cfg = cfg.get("time", {}) or {}
daily_timezone = str(time_cfg.get("daily_candle_timezone", "UTC"))
display_timezone = str(time_cfg.get("display_timezone", "Asia/Shanghai"))
forecast_raw = cfg.get("forecast", {}) or {}
market_data_cfg = cfg.get("market_data", {}) or {}
cache_dir = str(market_data_cfg.get("cache_dir", "data/cache/ohlcv"))
forecast_cfg = ForecastConfig(**forecast_raw)

st.title("下一根 K 线多空预测")
st.caption(
    "日常快速查看下一根 K 线更可能收涨还是收跌。预测基准始终是上一根已经完整收盘的 K 线，不使用实时价作为模型基准。"
)

with st.sidebar:
    st.header("快速预测")
    exchange = st.selectbox("交易所", ["gate", "binance", "okx", "bybit"], index=0)
    symbol = st.selectbox(
        "币种",
        ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT", "XRP/USDT"],
        index=0,
    )
    timeframe = st.selectbox(
        "预测周期",
        ["5m", "15m", "30m", "1h", "4h", "12h", "1d"],
        index=1,
        help="预测下一根该周期 K 线的收盘方向和收盘价。",
    )
    bars = int(
        st.slider(
            "训练使用最近 K 线",
            min_value=1000,
            max_value=10000,
            value=int(cfg.get("bars", 5000)),
            step=500,
        )
    )
    use_cache = st.checkbox(
        "使用本地历史缓存",
        value=bool(market_data_cfg.get("use_cache", True)),
        help="第一次下载完整窗口，以后只增量补最新 K 线。",
    )
    run = st.button("预测下一根 K 线", type="primary", use_container_width=True)

    if exchange == "gate" and timeframe == "1d":
        st.caption("Gate 日 K 按北京时间 **08:00 收盘**。")

if "quick_direction_row" not in st.session_state:
    st.session_state.quick_direction_row = None
    st.session_state.quick_direction_market = None
    st.session_state.quick_direction_error = None

if run:
    st.session_state.quick_direction_error = None
    try:
        with st.spinner(f"正在分析 {symbol} / {timeframe}..."):
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
            row = forecast.to_dict()
            row.update(
                {
                    "symbol": symbol,
                    "exchange": exchange,
                    "forecast_run_at": datetime.now(timezone.utc).isoformat(),
                    "candle_basis": basis,
                    "realized_model_weights": realized,
                }
            )
            save_forecast_run([row], results_dir)
            st.session_state.quick_direction_row = row
            st.session_state.quick_direction_market = market
    except Exception as exc:
        st.session_state.quick_direction_error = str(exc)

if st.session_state.quick_direction_error:
    st.error(f"预测失败：{st.session_state.quick_direction_error}")

row = st.session_state.quick_direction_row
market = st.session_state.quick_direction_market

if row is None:
    st.info("左侧选择币种和周期，然后点击“预测下一根 K 线”。")
else:
    view = build_direction_view(
        symbol=row["symbol"],
        base_price=row["current_price"],
        expected_price=row["expected_price"],
        expected_return_pct=row["expected_return_pct"],
        up_probability=row["up_probability"],
        neutral_threshold_bps=forecast_cfg.neutral_threshold_bps,
        strong_threshold_bps=forecast_cfg.strong_threshold_bps,
    )
    window = forecast_window(row["as_of"], row["timeframe"])
    base_close_text = format_display_timestamp(window["base_close_at"], display_timezone)
    target_close_text = format_display_timestamp(window["target_close_at"], display_timezone)

    st.subheader(f"{row['symbol']} · {row['timeframe']} · 下一根 K")
    if view.direction_code == "UP":
        st.success(f"### 预测方向：{view.direction_label} {view.arrow}")
    elif view.direction_code == "DOWN":
        st.error(f"### 预测方向：{view.direction_label} {view.arrow}")
    else:
        st.info(f"### 预测方向：{view.direction_label} {view.arrow}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("方向概率", f"{view.direction_probability:.1%}")
    c2.metric("预计收益率", f"{view.expected_return_pct:+.3f}%")
    c3.metric(
        f"预计价格变动（{view.quote_asset}）",
        f"{view.price_change:+,.4f}",
    )
    c4.metric("方向强度", view.strength_label)

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("上一根收盘 / 模型基准", f"{row['current_price']:,.4f}")
    c6.metric("预测下一根收盘", f"{row['expected_price']:,.4f}")
    try:
        live_price = load_live_price(row["exchange"], row["symbol"])
        c7.metric("实时价（仅参考）", f"{live_price:,.4f}")
    except Exception:
        c7.metric("实时价（仅参考）", "获取失败")
    c8.metric("模型置信度", f"{row['confidence']:.1%}")

    st.info(
        f"模型基准：**{base_close_text}** 已收盘价格 → 预测目标：**{target_close_text}** 收盘。"
    )
    st.caption(
        "预计收益率 =（预测下一根收盘价 - 上一根已收盘价格）/ 上一根已收盘价格。"
        "百分比是主指标；价格变动只是辅助显示，不代表杠杆后的账户收益。"
    )

    if view.probability_conflict:
        st.warning(
            "价格回归模型给出的方向与涨跌概率模型存在分歧。当前仍按预测收盘价相对上一根收盘价的方向显示，建议降低信任等级。"
        )

    if market is not None:
        refresh_mode = market.attrs.get("cache_refresh_mode", "-")
        cache_rows = market.attrs.get("cache_rows_total", len(market))
        downloaded = market.attrs.get("downloaded_rows_this_run", 0)
        st.caption(
            f"数据：{refresh_mode} ｜ 本地累计 {cache_rows} 根 ｜ 本次网络获取 {downloaded} 根 ｜ "
            f"历史方向验证准确率 {row['validation_direction_accuracy']:.1%} ｜ 模型一致度 {row['model_agreement']:.1%}"
        )

    with st.expander("查看详细研究信息"):
        st.write(f"上涨概率：{row['up_probability']:.1%}")
        st.write(f"近期波动率：{row['realized_volatility_pct']:.3f}%")
        st.write(f"验证 MAE：{row['validation_mae_pct']:.3f}%")
        st.write(f"训练样本：{row['training_rows']}")
        st.write(f"K线口径：{row.get('candle_basis', '-')}")

st.divider()
st.caption(
    "研究用途：这里的“收多/收空”表示模型预测下一根收盘价相对上一根已收盘价格的方向，不是自动交易指令。"
)
