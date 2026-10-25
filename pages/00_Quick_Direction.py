from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from quant_mm.auto_forecast import (
    find_existing_forecast,
    load_auto_settings,
    load_auto_status,
    save_auto_settings,
)
from quant_mm.dashboard_utils import (
    forecast_window,
    format_display_timestamp,
    load_forecast_history,
    save_forecast_run,
)
from quant_mm.data import fetch_closed_candle, fetch_last_price, forecast_candle_basis
from quant_mm.direction_view import build_direction_view
from quant_mm.forecast_engine import ForecastConfig, forecast_next_candle
from quant_mm.forecast_store import settle_due_forecasts
from quant_mm.market_cache import load_cached_forecast_ohlcv
from quant_mm.model_performance import realized_model_weights
from quant_mm.ui_labels import translate_scalar


CONFIG_PATH = Path("config_forecast.yaml")
PRESET_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT", "XRP/USDT"]
TIMEFRAMES = ["5m", "15m", "30m", "1h", "4h", "12h", "1d"]


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


def fmt_price(value: float) -> str:
    value = float(value)
    absolute = abs(value)
    if absolute >= 1000:
        return f"{value:,.2f}"
    if absolute >= 1:
        return f"{value:,.4f}"
    return f"{value:,.6f}"


def fmt_return(value: float) -> str:
    value = float(value)
    absolute = abs(value)
    digits = 2 if absolute >= 1 else 3 if absolute >= 0.1 else 4
    return f"{value:+.{digits}f}%"


def render_metric_grid(items: list[tuple[str, str, str]]) -> None:
    cards = []
    for label, value, note in items:
        cards.append(
            "<div class='q-card'>"
            f"<div class='q-label'>{escape(label)}</div>"
            f"<div class='q-value'>{escape(value)}</div>"
            f"<div class='q-note'>{escape(note)}</div>"
            "</div>"
        )
    st.markdown("<div class='q-grid'>" + "".join(cards) + "</div>", unsafe_allow_html=True)


def history_table(history: pd.DataFrame, display_timezone: str) -> pd.DataFrame:
    if history.empty:
        return history
    rows = []
    for item in history.to_dict("records"):
        expected_return = float(item.get("expected_return_pct") or 0.0)
        actual_return = item.get("actual_return_pct")
        status = str(translate_scalar(item.get("status") or "PENDING"))
        direction = "收涨（多） ↑" if expected_return > 0 else "收跌（空） ↓" if expected_return < 0 else "基本持平 →"
        hit = item.get("direction_correct")
        hit_text = "✅" if hit == 1 else "❌" if hit == 0 else "—"
        rows.append(
            {
                "目标收盘时间": format_display_timestamp(item.get("target_close_at"), display_timezone),
                "状态": status,
                "预测方向": direction,
                "预测收益率": fmt_return(expected_return),
                "预测收盘价": fmt_price(item.get("expected_price") or 0.0),
                "实际收益率": fmt_return(float(actual_return)) if actual_return is not None else "—",
                "实际收盘价": fmt_price(float(item["actual_close"])) if item.get("actual_close") is not None else "—",
                "方向命中": hit_text,
            }
        )
    return pd.DataFrame(rows)


st.markdown(
    """
<style>
.block-container {max-width: 1280px; padding-top: 1.4rem; padding-bottom: 3rem;}
.q-hero {border-radius: 20px; padding: 24px 28px; margin: 12px 0 18px 0; border: 1px solid rgba(255,255,255,.08);}
.q-hero.up {background: linear-gradient(135deg, rgba(22,199,132,.26), rgba(22,199,132,.08));}
.q-hero.down {background: linear-gradient(135deg, rgba(234,57,67,.26), rgba(234,57,67,.08));}
.q-hero.flat {background: linear-gradient(135deg, rgba(240,185,11,.22), rgba(240,185,11,.06));}
.q-hero-kicker {font-size: 14px; opacity: .72; margin-bottom: 6px;}
.q-hero-title {font-size: clamp(28px, 4vw, 46px); font-weight: 800; line-height: 1.15;}
.q-grid {display: grid; grid-template-columns: repeat(auto-fit, minmax(205px, 1fr)); gap: 12px; margin: 12px 0 18px 0;}
.q-card {border: 1px solid rgba(255,255,255,.09); background: rgba(255,255,255,.025); border-radius: 16px; padding: 17px 18px; min-height: 112px; overflow: hidden;}
.q-label {font-size: 13px; opacity: .68; margin-bottom: 7px;}
.q-value {font-size: clamp(21px, 2.2vw, 31px); font-weight: 720; line-height: 1.15; white-space: normal; overflow-wrap: anywhere;}
.q-note {font-size: 12px; opacity: .55; margin-top: 8px; line-height: 1.35;}
.q-section {font-size: 20px; font-weight: 760; margin-top: 24px; margin-bottom: 8px;}
.q-strip {border-radius: 14px; padding: 12px 16px; background: rgba(56,139,253,.12); border: 1px solid rgba(56,139,253,.2); margin: 10px 0 16px 0;}
@media (max-width: 780px) {.q-card {min-height: 96px;} .q-grid {grid-template-columns: 1fr 1fr;}}
</style>
""",
    unsafe_allow_html=True,
)

cfg = load_config()
results_dir = str(cfg.get("results_dir", "results/forecast"))
time_cfg = cfg.get("time", {}) or {}
daily_timezone = str(time_cfg.get("daily_candle_timezone", "UTC"))
display_timezone = str(time_cfg.get("display_timezone", "Asia/Shanghai"))
forecast_raw = cfg.get("forecast", {}) or {}
market_data_cfg = cfg.get("market_data", {}) or {}
cache_dir = str(market_data_cfg.get("cache_dir", "data/cache/ohlcv"))
forecast_cfg = ForecastConfig(**forecast_raw)
auto_settings = load_auto_settings(results_dir)

st.title("下一根 K 线多空预测")
st.caption("日常只看最重要的结果：下一根更可能收涨还是收跌、方向概率、预计收益率和目标收盘价。")

with st.sidebar:
    st.header("快速预测")
    exchange = st.selectbox("交易所", ["gate", "binance", "okx", "bybit"], index=0)
    symbol = st.selectbox("币种", PRESET_SYMBOLS, index=0)
    timeframe = st.selectbox(
        "预测周期",
        TIMEFRAMES,
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
    run = st.button("立即预测下一根 K 线", type="primary", use_container_width=True)

    if exchange == "gate" and timeframe == "1d":
        st.caption("Gate 日 K 按北京时间 **08:00 收盘**。")

    st.divider()
    st.subheader("自动预测")
    with st.form("auto_forecast_settings"):
        auto_enabled = st.toggle("启用自动预测", value=bool(auto_settings.get("enabled", False)))
        auto_symbols = st.multiselect(
            "自动预测币种",
            PRESET_SYMBOLS,
            default=[s for s in auto_settings.get("symbols", [symbol]) if s in PRESET_SYMBOLS] or [symbol],
        )
        auto_timeframes = st.multiselect(
            "自动预测周期",
            TIMEFRAMES,
            default=[t for t in auto_settings.get("timeframes", [timeframe]) if t in TIMEFRAMES] or [timeframe],
        )
        poll_options = [60, 120, 300, 600, 900]
        saved_poll = int(auto_settings.get("poll_seconds", 60))
        poll_index = poll_options.index(saved_poll) if saved_poll in poll_options else 0
        poll_seconds = st.selectbox(
            "后台检查频率",
            poll_options,
            index=poll_index,
            format_func=lambda x: f"每 {x // 60} 分钟检查",
        )
        save_auto = st.form_submit_button("保存自动预测设置", use_container_width=True)

    if save_auto:
        save_auto_settings(
            results_dir,
            {
                "enabled": auto_enabled,
                "exchange": exchange,
                "symbols": auto_symbols,
                "timeframes": auto_timeframes,
                "bars": bars,
                "use_cache": use_cache,
                "poll_seconds": poll_seconds,
            },
        )
        auto_settings = load_auto_settings(results_dir)
        st.success("自动预测设置已保存。")

    if auto_settings.get("enabled"):
        st.success("自动预测：已开启")
        st.caption("只在出现新的已收盘 K 线时记录一次；相同目标不会重复预测或覆盖。")
    else:
        st.caption("自动预测当前关闭。")

    worker_status = load_auto_status(results_dir)
    if worker_status.get("finished_at"):
        st.caption(
            f"最近后台检查：{format_display_timestamp(worker_status['finished_at'], display_timezone, include_seconds=True)}"
        )

if "quick_direction_row" not in st.session_state:
    st.session_state.quick_direction_row = None
    st.session_state.quick_direction_market = None
    st.session_state.quick_direction_error = None
    st.session_state.quick_direction_reused = False

if run:
    st.session_state.quick_direction_error = None
    st.session_state.quick_direction_reused = False
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
            if market is None or market.empty:
                raise ValueError("没有可用的已收盘 K 线")

            existing = find_existing_forecast(
                results_dir,
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                as_of=market.index[-1],
            )
            if existing is not None:
                row = existing
                st.session_state.quick_direction_reused = True
            else:
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
                        "forecast_source": "manual",
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
    st.info("左侧选择币种和周期，然后点击“立即预测下一根 K 线”。")
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

    hero_class = "up" if view.direction_code == "UP" else "down" if view.direction_code == "DOWN" else "flat"
    st.markdown(
        f"<div class='q-hero {hero_class}'>"
        f"<div class='q-hero-kicker'>{escape(row['symbol'])} · {escape(row['timeframe'])} · 下一根 K</div>"
        f"<div class='q-hero-title'>预测方向：{escape(view.direction_label)} {escape(view.arrow)}</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    if st.session_state.quick_direction_reused:
        st.info("这个目标 K 线已经有正式预测，本次直接显示第一次保存的结果，没有重新生成或覆盖。")

    render_metric_grid(
        [
            ("方向概率", f"{view.direction_probability:.1%}", "与当前预测方向对应的概率"),
            ("预计收益率", fmt_return(view.expected_return_pct), "相对上一根已收盘价格"),
            (f"预计价格变动（{view.quote_asset}）", f"{view.price_change:+,.2f}", "绝对价格变化，仅作辅助"),
            ("预测下一根收盘", fmt_price(row["expected_price"]), target_close_text),
        ]
    )

    try:
        live_price = load_live_price(row["exchange"], row["symbol"])
        live_text = fmt_price(live_price)
    except Exception:
        live_text = "获取失败"

    render_metric_grid(
        [
            ("上一根收盘 / 模型基准", fmt_price(row["current_price"]), base_close_text),
            ("实时价（仅参考）", live_text, "实时价不参与这条预测的基准"),
            ("模型置信度", f"{float(row.get('confidence', 0.0)):.1%}", "模型综合置信度"),
            ("方向强度", view.strength_label, "按预测涨跌幅强弱划分"),
        ]
    )

    st.markdown(
        f"<div class='q-strip'><b>预测时间窗口：</b>{escape(base_close_text)} 已收盘 → "
        f"预测 {escape(target_close_text)} 收盘。预计收益率以百分比为主，价格变动只作为辅助。</div>",
        unsafe_allow_html=True,
    )

    if view.probability_conflict:
        st.warning("价格模型与涨跌概率模型方向存在分歧，当前仍按预测收盘价判断收涨/收跌，建议降低信任等级。")

    refresh_mode = market.attrs.get("cache_refresh_mode", "-") if market is not None else "-"
    cache_rows = market.attrs.get("cache_rows_total", len(market)) if market is not None else 0
    downloaded = market.attrs.get("downloaded_rows_this_run", 0) if market is not None else 0
    basis_text = str(translate_scalar(row.get("candle_basis", "-")))

    st.markdown("<div class='q-section'>模型与数据参考</div>", unsafe_allow_html=True)
    render_metric_grid(
        [
            ("历史方向验证准确率", f"{float(row.get('validation_direction_accuracy', 0.0)):.1%}", "时间顺序验证集"),
            ("模型一致度", f"{float(row.get('model_agreement', 0.0)):.1%}", "多个回归模型方向/结果的一致程度"),
            ("验证 MAE", f"{float(row.get('validation_mae_pct', 0.0)):.3f}%", "验证阶段平均绝对误差"),
            ("训练样本", f"{int(row.get('training_rows', 0)):,}", "本次实际进入训练的数据量"),
        ]
    )
    st.caption(
        f"数据缓存：{translate_scalar(refresh_mode)} ｜ 本地累计 {cache_rows} 根 ｜ 本次网络获取 {downloaded} 根 ｜ K线口径：{basis_text}"
    )

st.divider()
st.subheader("预测记录")
st.caption("同一交易所 + 币种 + 周期 + 基准 K 线只保存第一次正式预测；重复点击不会新增或覆盖。")


@st.fragment(run_every="60s")
def quick_history_panel() -> None:
    try:
        settle_due_forecasts(results_dir, candle_fetcher=fetch_closed_candle, limit=100)
    except Exception:
        pass

    history = load_forecast_history(results_dir, limit=300)
    if history.empty:
        st.caption("还没有预测记录。")
        return

    filtered = history[
        (history.get("symbol") == symbol)
        & (history.get("timeframe") == timeframe)
        & (history.get("exchange") == exchange)
    ].copy()
    if filtered.empty:
        st.caption(f"{symbol} / {timeframe} 还没有预测记录。")
        return

    st.dataframe(
        history_table(filtered.head(30), display_timezone),
        use_container_width=True,
        hide_index=True,
    )


quick_history_panel()

st.caption("研究用途：这里的“收涨/收跌”表示模型预测下一根收盘价相对上一根已收盘价格的方向，不是自动交易指令。")
