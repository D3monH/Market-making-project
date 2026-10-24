from quant_mm.data import (
    OHLCV_LOOKBACK_SAFETY_POINTS,
    _initial_since_ms,
    ohlcv_history_point_limit,
)


def test_gate_history_limit_is_known_for_current_and_legacy_ids():
    assert ohlcv_history_point_limit("gate") == 10_000
    assert ohlcv_history_point_limit("gateio") == 10_000


def test_gate_large_request_is_clamped_inside_venue_boundary():
    now_ms = 2_000_000_000_000
    timeframe_ms = 4 * 60 * 60 * 1000
    limit = ohlcv_history_point_limit("gate")

    # 5,000 Beijing natural days need >30,000 4h source candles. The old
    # implementation tried to start that far back and Gate rejected the call.
    since = _initial_since_ms(
        now_ms,
        timeframe_ms,
        bars=30_018,
        max_lookback_points=limit,
    )
    points_back = (now_ms - since) // timeframe_ms

    assert points_back == limit - OHLCV_LOOKBACK_SAFETY_POINTS
    assert points_back < 10_000


def test_unbounded_exchange_keeps_normal_gap_buffer():
    now_ms = 2_000_000_000_000
    timeframe_ms = 15 * 60 * 1000
    since = _initial_since_ms(now_ms, timeframe_ms, bars=5000)

    points_back = (now_ms - since) // timeframe_ms
    assert points_back == 5500
