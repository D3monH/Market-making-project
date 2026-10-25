from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from quant_mm.dashboard_utils import format_display_timestamp, timeframe_to_timedelta
from quant_mm.event_contract_engine import build_event_probability, calculate_market_edge
from quant_mm.event_contract_store import (
    event_accuracy_summary,
    get_existing_event,
    load_event_records,
    manually_settle_event,
    persist_event_prediction,
    settle_due_index_events,
)
from quant_mm.forecast_engine import ForecastConfig, forecast_next_candle
from quant_mm.gate_event_data import (
    EVENT_ASSETS,
    EVENT_TIMEFRAMES,
    fetch_gate_index_candle,
    load_gate_index_market,
    supports_index_market,
)
from quant_mm.institutional_alpha import build_institutional_alpha, fetch_gate_derivatives_context
from quant_mm.market_cache import load_cached_forecast_ohlcv


CONFIG_PATH = Path("config_forecast.yaml")
EVENT_RESULTS_DIR = Path("results/event_contracts")
EVENT_CACHE_DIR = Path("data/cache/event_contracts")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@st.cache_data(ttl=45, show_spinner=False)
def load_index_market(asset: str, timeframe: str, bars: int):
    return load_gate_index_market(
        asset,
        timeframe,
        bars=bars,
        cache_dir=EVENT_CACHE_DIR,
    )


@st.cache_data(ttl=60, show_spinner=False)
def load_twap_proxy(symbol: str, timeframe: str, bars: int, daily_timezone: str, cache_dir: str):
    return load_cached_forecast_ohlcv(
        "gate",
        symbol,
        timeframe,
        bars,
        daily_timezone=daily_timezone,
        use_cache=True,
        cache_dir=cache_dir,
    )


def side_label(side: str) -> str:
    return {"UP": "看涨 UP ↑", "DOWN": "看跌 DOWN ↓", "WAIT": "等待 / 无明显优势"}.get(str(side), str(side))


def status_label(status: str) -> str:
    return {
        "PENDING": "等待 INDEX 到期验真",
        "SETTLED": "已自动验真",
        "SETTLED_MANUAL": "已手动验真",
        "RESEARCH_ONLY": "TWAP 研究模式（待手动验真）",
    }.get(str(status), str(status))


def event_record_view(records: pd.DataFrame, display_timezone: str) -> pd.DataFrame:
    if records.empty:
        return records
    shown = records.copy()
    for column in ("forecast_run_at", "event_open_at", "event_close_at"):
        if column in shown:
            shown[column] = shown[column].map(lambda x: format_display_timestamp(x, display_timezone))
    if "predicted_side" in shown:
        shown["predicted_side"] = shown["predicted_side"].map(side_label)
    if "actual_side" in shown:
        shown["actual_side"] = shown["actual_side"].map(lambda x: "—" if pd.isna(x) else side_label(x))
    if "status" in shown:
        shown["status"] = shown["status"].map(status_label)
    columns = [
        "asset", "timeframe", "benchmark", "event_close_at", "status",
        "final_up_probability", "predicted_side", "up_market_price", "down_market_price",
        "expected_edge", "actual_side", "direction_correct", "brier_score",
        "realized_pnl_per_share",
    ]
    columns = [c for c in columns if c in shown.columns]
    shown = shown[columns].rename(
        columns={
            "asset": "标的",
            "timeframe": "周期",
            "benchmark": "基准",
            "event_close_at": "事件结束",
            "status": "状态",
            "final_up_probability": "最终P(UP)",
            "predicted_side": "模型判断",
            "up_market_price": "UP市场价",
            "down_market_price": "DOWN市场价",
            "expected_edge": "最佳Edge",
            "actual_side": "实际结果",
            "direction_correct": "方向命中",
            "brier_score": "Brier",
            "realized_pnl_per_share": "每份实际PnL",
        }
    )
    for c in ("最终P(UP)",):
        if c in shown:
            shown[c] = shown[c].map(lambda x: "—" if pd.isna(x) else f"{float(x):.1%}")
    for c in ("最佳Edge", "每份实际PnL"):
        if c in shown:
            shown[c] = shown[c].map(lambda x: "—" if pd.isna(x) else f"{float(x):+.4f}")
    if "Brier" in shown:
        shown["Brier"] = shown["Brier"].map(lambda x: "—" if pd.isna(x) else f"{float(x):.4f}")
    if "方向命中" in shown:
        shown["方向命中"] = shown["方向命中"].map(
            lambda x: "—" if pd.isna(x) else ("✅" if int(x) == 1 else "❌")
        )
    return shown


cfg = load_config()
time_cfg = cfg.get("time", {}) or {}
daily_timezone = str(time_cfg.get("daily_candle_timezone", "UTC"))
display_timezone = str(time_cfg.get("display_timezone", "Asia/Shanghai"))
market_data_cfg = cfg.get("market_data", {}) or {}
spot_cache_dir = str(market_data_cfg.get("cache_dir", "data/cache/ohlcv"))
forecast_cfg = ForecastConfig(**(cfg.get("forecast", {}) or {}))

# Auto-settle matured INDEX records whenever this page is used.
try:
    settle_due_index_events(EVENT_RESULTS_DIR, fetch_gate_index_candle, limit=50)
except Exception:
    pass

st.title("🎯 Gate 加密货币事件合约研究中心")
st.caption(
    "专门研究 Gate 加密货币涨跌事件：模型给出 P(UP)/P(DOWN)，机构 Alpha 做独立校准，"
    "再与 Gate 事件合约市场价格比较 Edge / EV。当前只做研究与验真，不自动下单。"
)

with st.sidebar:
    st.header("事件合约设置")
    asset = st.selectbox("标的", list(EVENT_ASSETS), index=0)
    timeframe = st.selectbox("事件周期", list(EVENT_TIMEFRAMES), index=1)
    available_benchmarks = ["TWAP"]
    if supports_index_market(asset, timeframe):
        available_benchmarks.insert(0, "INDEX")
    benchmark = st.selectbox("价格基准", available_benchmarks, index=0)

    if benchmark == "INDEX":
        st.caption("官方 INDEX：Gate 合约指数价格。当前官方事件市场覆盖 BTC/ETH 的 5m、15m。")
        max_bars = 1900
        default_bars = 1600
    else:
        st.caption("TWAP：官方结算来自 Chainlink 60秒 TWAP。当前页面使用 Gate 行情做研究代理，不冒充官方 TWAP。")
        max_bars = 5000
        default_bars = 3000

    bars = int(st.slider("训练历史 K 线", min_value=900, max_value=max_bars, value=min(default_bars, max_bars), step=100))
    use_alpha = st.checkbox("使用机构 Alpha 校准概率", value=True)

    st.divider()
    st.subheader("Gate 事件盘口（可选）")
    use_market_prices = st.checkbox(
        "我手动填写当前 UP / DOWN 买入价",
        value=False,
        help="Gate 当前未在公开 API v4 文档中提供事件合约盘口接口，所以第一版采用手动价格。",
    )
    up_price = None
    down_price = None
    if use_market_prices:
        up_price = float(st.number_input("UP 买入价 (USDT/份)", min_value=0.01, max_value=0.99, value=0.50, step=0.01))
        down_price = float(st.number_input("DOWN 买入价 (USDT/份)", min_value=0.01, max_value=0.99, value=0.50, step=0.01))

    twap_reference = None
    if benchmark == "TWAP":
        raw_reference = float(
            st.number_input(
                "Gate 页面事件基准价（可选）",
                min_value=0.0,
                value=0.0,
                step=1.0,
                help="若你从 Gate 事件页看到本场 TWAP 基准价，可填入；0 表示使用模型行情代理价，仅作研究。",
            )
        )
        twap_reference = raw_reference if raw_reference > 0 else None

    run = st.button("运行事件合约预测", type="primary", use_container_width=True)

if "event_contract_current" not in st.session_state:
    st.session_state.event_contract_current = None
    st.session_state.event_contract_error = None

if run:
    st.session_state.event_contract_error = None
    try:
        symbol = f"{asset}/USDT"
        with st.spinner(f"正在分析 {asset} {timeframe} {benchmark}..."):
            if benchmark == "INDEX":
                market = load_index_market(asset, timeframe, bars)
                data_mode = "official_gate_index"
            else:
                market = load_twap_proxy(symbol, timeframe, bars, daily_timezone, spot_cache_dir)
                data_mode = "twap_proxy_gate_spot"

            delta = timeframe_to_timedelta(timeframe)
            base_candle_open = pd.Timestamp(market.index[-1])
            if base_candle_open.tzinfo is None:
                base_candle_open = base_candle_open.tz_localize("UTC")
            else:
                base_candle_open = base_candle_open.tz_convert("UTC")
            event_open_at = base_candle_open + delta
            event_close_at = event_open_at + delta

            existing = get_existing_event(
                EVENT_RESULTS_DIR,
                asset=asset,
                timeframe=timeframe,
                benchmark=benchmark,
                event_open_at=event_open_at,
            )
            if existing is not None:
                st.session_state.event_contract_current = existing
                st.info("这一场事件已经生成过正式预测，已直接读取第一次保存的结果，避免重复预测/挑结果。")
            else:
                forecast = forecast_next_candle(market, timeframe=timeframe, cfg=forecast_cfg)

                event_reference_price = twap_reference
                index_open = None
                if benchmark == "INDEX":
                    try:
                        current_event_candle = fetch_gate_index_candle(asset, timeframe, event_open_at)
                        if current_event_candle:
                            index_open = float(current_event_candle["open"])
                            event_reference_price = index_open
                    except Exception:
                        pass
                if event_reference_price is None:
                    event_reference_price = float(forecast.current_price)

                alpha = None
                if use_alpha:
                    derivatives = fetch_gate_derivatives_context(symbol, timeframe)
                    alpha = build_institutional_alpha(
                        market,
                        expected_return_pct=forecast.expected_return_pct,
                        model_confidence=forecast.confidence,
                        derivatives_context=derivatives,
                        market_regime=forecast.market_regime,
                    )

                probability = build_event_probability(
                    forecast,
                    alpha,
                    event_reference_price=event_reference_price,
                )
                edge = calculate_market_edge(
                    probability.final_up_probability,
                    up_price=up_price,
                    down_price=down_price,
                )

                payload = {
                    "exchange": "gate",
                    "asset": asset,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "benchmark": benchmark,
                    "data_mode": data_mode,
                    "forecast_run_at": datetime.now(timezone.utc).isoformat(),
                    "event_open_at": event_open_at.isoformat(),
                    "event_close_at": event_close_at.isoformat(),
                    "model_base_price": float(forecast.current_price),
                    "event_reference_price": float(event_reference_price),
                    "expected_price": float(forecast.expected_price),
                    "expected_return_pct": float(probability.event_expected_return_pct),
                    "raw_up_probability": float(probability.model_up_probability),
                    "final_up_probability": float(probability.final_up_probability),
                    "final_down_probability": float(probability.final_down_probability),
                    "predicted_side": probability.predicted_side,
                    "confidence": probability.confidence,
                    "alpha_score": None if alpha is None else float(alpha.composite_score),
                    "alpha_confidence": None if alpha is None else float(alpha.consensus_confidence),
                    "alpha_data_quality": None if alpha is None else float(alpha.data_quality),
                    "up_market_price": up_price,
                    "down_market_price": down_price,
                    "selected_side": edge.best_side,
                    "expected_edge": edge.best_edge,
                    "expected_roi_pct": edge.best_roi_pct,
                    "status": "PENDING" if benchmark == "INDEX" else "RESEARCH_ONLY",
                    "forecast": forecast.to_dict(),
                    "event_probability": probability.to_dict(),
                    "alpha": None if alpha is None else alpha.to_dict(),
                    "market_edge": edge.to_dict(),
                    "official_event_reference_available": index_open is not None if benchmark == "INDEX" else twap_reference is not None,
                }
                eid, inserted = persist_event_prediction(payload, EVENT_RESULTS_DIR)
                payload["event_id"] = eid
                st.session_state.event_contract_current = payload
                if not inserted:
                    existing = get_existing_event(
                        EVENT_RESULTS_DIR,
                        asset=asset,
                        timeframe=timeframe,
                        benchmark=benchmark,
                        event_open_at=event_open_at,
                    )
                    if existing:
                        st.session_state.event_contract_current = existing
    except Exception as exc:
        st.session_state.event_contract_error = str(exc)

if st.session_state.event_contract_error:
    st.error(f"事件预测失败：{st.session_state.event_contract_error}")

row = st.session_state.event_contract_current
if row is None:
    st.info("左侧选择标的、周期和 INDEX/TWAP 基准，然后点击“运行事件合约预测”。")
else:
    raw = row
    if isinstance(row.get("raw_json"), str):
        try:
            raw = {**row, **json.loads(row["raw_json"])}
        except Exception:
            raw = row

    p_up = float(row.get("final_up_probability", raw.get("final_up_probability", 0.5)))
    p_down = float(row.get("final_down_probability", raw.get("final_down_probability", 1.0 - p_up)))
    predicted_side = str(row.get("predicted_side", raw.get("predicted_side", "WAIT")))
    confidence = float(row.get("confidence", raw.get("confidence", 0.0)))
    event_open_text = format_display_timestamp(row.get("event_open_at", raw.get("event_open_at")), display_timezone)
    event_close_text = format_display_timestamp(row.get("event_close_at", raw.get("event_close_at")), display_timezone)

    st.subheader(f"{row.get('asset', raw.get('asset'))} · {row.get('timeframe', raw.get('timeframe'))} · {row.get('benchmark', raw.get('benchmark'))}")
    if predicted_side == "UP":
        st.success(f"### 模型判断：{side_label(predicted_side)}")
    elif predicted_side == "DOWN":
        st.error(f"### 模型判断：{side_label(predicted_side)}")
    else:
        st.info(f"### 模型判断：{side_label(predicted_side)}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("最终 P(UP)", f"{p_up:.1%}")
    c2.metric("最终 P(DOWN)", f"{p_down:.1%}")
    c3.metric("概率置信度", f"{confidence:.1%}")
    c4.metric("事件预期涨跌", f"{float(row.get('expected_return_pct', raw.get('expected_return_pct', 0.0))):+.3f}%")

    c5, c6, c7 = st.columns(3)
    c5.metric("模型基准价", f"{float(row.get('model_base_price', raw.get('model_base_price', 0.0))):,.4f}")
    c6.metric("事件基准价", f"{float(raw.get('event_reference_price', row.get('model_base_price', 0.0))):,.4f}")
    c7.metric("模型预测结算价", f"{float(row.get('expected_price', raw.get('expected_price', 0.0))):,.4f}")

    st.info(f"事件窗口：**{event_open_text} → {event_close_text}**（{row.get('benchmark', raw.get('benchmark'))}）")

    alpha_score = row.get("alpha_score", raw.get("alpha_score"))
    if alpha_score is not None:
        a1, a2, a3 = st.columns(3)
        a1.metric("机构 Alpha 综合分", f"{float(alpha_score):+.3f}")
        a2.metric("Alpha 置信度", f"{float(row.get('alpha_confidence', raw.get('alpha_confidence', 0.0))):.1%}")
        a3.metric("Alpha 数据完整度", f"{float(row.get('alpha_data_quality', raw.get('alpha_data_quality', 0.0))):.1%}")
        st.caption("Alpha 只做有界概率校准：Funding/OI/Basis/持仓/盘口等不会直接覆盖价格模型，也不会让概率无限放大。")

    market_edge = raw.get("market_edge") or {}
    if row.get("up_market_price") is not None or market_edge:
        st.subheader("市场概率与模型 Edge")
        up_mkt = row.get("up_market_price", market_edge.get("up_price"))
        down_mkt = row.get("down_market_price", market_edge.get("down_price"))
        best_edge = row.get("expected_edge", market_edge.get("best_edge"))
        best_roi = row.get("expected_roi_pct", market_edge.get("best_roi_pct"))
        e1, e2, e3, e4 = st.columns(4)
        e1.metric("Gate UP 市场价", "—" if up_mkt is None else f"{float(up_mkt):.2f}")
        e2.metric("Gate DOWN 市场价", "—" if down_mkt is None else f"{float(down_mkt):.2f}")
        e3.metric("最佳毛 Edge/份", "—" if best_edge is None else f"{float(best_edge):+.4f} USDT")
        e4.metric("理论毛 ROI", "—" if best_roi is None else f"{float(best_roi):+.1f}%")
        st.caption("Edge/ROI 未扣 Gate 动态撮合手续费；仅用于比较模型概率与当前市场隐含概率。")

    if str(row.get("benchmark", raw.get("benchmark"))).upper() == "TWAP":
        st.warning(
            "TWAP 官方结算价格来自 Chainlink 60 秒时间加权价格。当前 Gate API v4 未公开事件合约 TWAP 历史/盘口接口，"
            "所以本页的 TWAP 模型行情是研究代理，不能把它当成官方自动验真结果。"
        )

st.divider()
st.subheader("事件合约真实战绩")
summary = event_accuracy_summary(EVENT_RESULTS_DIR, asset=asset)
if summary.empty:
    st.caption("还没有足够的已验真事件。INDEX 到期后可自动验真；TWAP 可在下方手动录入 Gate 最终结果。")
else:
    shown = summary.copy()
    shown["direction_accuracy"] = shown["direction_accuracy"].map(lambda x: f"{float(x):.1%}")
    shown["brier_score"] = shown["brier_score"].map(lambda x: f"{float(x):.4f}")
    shown["log_loss"] = shown["log_loss"].map(lambda x: f"{float(x):.4f}")
    shown["avg_confidence"] = shown["avg_confidence"].map(lambda x: f"{float(x):.1%}")
    shown = shown.rename(
        columns={
            "asset": "标的", "timeframe": "周期", "benchmark": "基准", "samples": "样本",
            "direction_accuracy": "方向命中率", "brier_score": "平均Brier", "log_loss": "平均LogLoss",
            "avg_confidence": "平均置信度", "avg_realized_pnl_per_share": "平均每份PnL",
        }
    )
    st.dataframe(shown, use_container_width=True, hide_index=True)

records = load_event_records(EVENT_RESULTS_DIR, asset=asset, limit=80)
st.subheader("最近事件预测记录")
if records.empty:
    st.caption("暂无事件预测。")
else:
    st.dataframe(event_record_view(records, display_timezone), use_container_width=True, hide_index=True)

    twap_pending = records.loc[records["status"] == "RESEARCH_ONLY"] if "status" in records else pd.DataFrame()
    if not twap_pending.empty:
        with st.expander("手动验真 TWAP 事件"):
            options = {
                f"{r['event_id'][:8]} · {r['asset']} {r['timeframe']} · {format_display_timestamp(r['event_close_at'], display_timezone)}": r["event_id"]
                for _, r in twap_pending.iterrows()
            }
            selected_label = st.selectbox("选择已结束的 TWAP 预测记录", list(options))
            actual_side = st.radio("Gate 最终结果", ["UP", "DOWN"], horizontal=True)
            if st.button("保存 TWAP 手动验真"):
                manually_settle_event(EVENT_RESULTS_DIR, options[selected_label], actual_side=actual_side)
                st.success("已保存手动验真结果。")
                st.rerun()

st.divider()
st.caption(
    "研究用途：Gate 事件合约每份正确结果按规则结算价值为 1 USDT、错误结果归零；"
    "页面中的概率、Edge 和 EV 是研究指标，不是自动交易指令。"
)
