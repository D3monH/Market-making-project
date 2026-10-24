from quant_mm.gate_execution import GateExecutionConfig, GateOrderExecutor, build_spot_limit_order, sign_gate_request
from quant_mm.gate_stream import BookTicker, PublicTrade, parse_gate_message
from quant_mm.market_maker import MarketMakerConfig
from quant_mm.microstructure import MicroAlphaConfig, RollingTradeFlow, book_imbalance, micro_signal_bps
from quant_mm.online_model import OnlineLinearConfig, OnlineMicroLinearModel
from quant_mm.paper_live import LivePaperConfig, LivePaperEngine


def test_parse_gate_book_and_trade():
    b = parse_gate_message({"event":"update","channel":"spot.book_ticker","result":{"s":"BTC_USDT","b":"100","B":"4","a":"101","A":"1","t":123,"u":9}})
    assert isinstance(b, BookTicker)
    assert b.mid == 100.5
    t = parse_gate_message({"event":"update","channel":"spot.trades","result":{"currency_pair":"BTC_USDT","side":"buy","price":"101","amount":"0.2","create_time_ms":"124.5","id":7}})
    assert isinstance(t, PublicTrade)
    assert abs(t.notional - 20.2) < 1e-12


def test_microstructure_signal_direction():
    book = BookTicker("BTC_USDT", 100, 9, 101, 1, 1)
    flow = RollingTradeFlow(10)
    flow.add(PublicTrade("BTC_USDT", "buy", 101, 1, 1, "1"))
    cfg = MicroAlphaConfig(book_weight_bps=4, trade_flow_weight_bps=4, max_micro_signal_bps=8)
    assert book_imbalance(book) > 0
    assert micro_signal_bps(book, flow, cfg) > 0


def test_online_model_labels_only_after_horizon():
    cfg = OnlineLinearConfig(horizon_ms=1000, min_sample_interval_ms=100, min_train_samples=3, retrain_every=1, train_window=20)
    model = OnlineMicroLinearModel(cfg)
    flow = RollingTradeFlow(10)
    for i in range(5):
        model.observe(BookTicker("BTC_USDT", 99.9+i*0.1, 2, 100.1+i*0.1, 1, i*200), flow, 0.001)
    assert model.train_samples == 0
    model.observe(BookTicker("BTC_USDT", 100.9, 2, 101.1, 1, 1200), flow, 0.001)
    assert model.train_samples > 0


def test_paper_spot_never_sells_more_than_inventory():
    mm = MarketMakerConfig(order_notional_usdt=100, max_inventory_usdt=1000, base_half_spread_bps=1, min_half_spread_bps=1, vol_spread_multiplier=0, signal_weight=0)
    paper = LivePaperConfig(initial_equity_usdt=1000, initial_base_inventory_usdt=50, min_quote_interval_ms=0, strict_crossing_fill=True, allow_short=False)
    eng = LivePaperEngine(mm, paper, MicroAlphaConfig(book_weight_bps=0, trade_flow_weight_bps=0), OnlineLinearConfig(min_train_samples=9999))
    q = eng.on_book(BookTicker("BTC_USDT", 99.9, 1, 100.1, 1, 1000))
    assert q is not None
    initial_qty = eng.inventory_qty
    fill = eng.on_trade(PublicTrade("BTC_USDT","buy",q.ask+0.01,10,1001,"y"))
    assert fill is not None
    assert fill["qty"] <= initial_qty
    assert eng.inventory_qty >= 0


def test_live_execution_is_double_gated(monkeypatch):
    monkeypatch.setenv("GATE_API_KEY", "k")
    monkeypatch.setenv("GATE_API_SECRET", "s")
    monkeypatch.delenv("ALLOW_GATE_LIVE_TRADING", raising=False)
    ex = GateOrderExecutor(GateExecutionConfig(mode="live"))
    try:
        ex._assert_private_enabled()
    except RuntimeError as e:
        assert "live trading blocked" in str(e)
    else:
        raise AssertionError("live trading should be blocked")


def test_gate_signature_is_deterministic():
    h1 = sign_gate_request("k", "s", "POST", "/api/v4/spot/orders", body="{}", timestamp="1700000000")
    h2 = sign_gate_request("k", "s", "POST", "/api/v4/spot/orders", body="{}", timestamp="1700000000")
    assert h1 == h2
    assert len(h1["SIGN"]) == 128


def test_order_payload_symbol_format():
    p = build_spot_limit_order("BTC/USDT", "buy", 0.001, 65000)
    assert p["currency_pair"] == "BTC_USDT"
    assert p["time_in_force"] == "gtc"
