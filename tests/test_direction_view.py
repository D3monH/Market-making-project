from __future__ import annotations

import pytest

from quant_mm.direction_view import build_direction_view


def test_up_direction_uses_up_probability_and_quote_price_change():
    view = build_direction_view(
        symbol="BTC/USDT",
        base_price=80000.0,
        expected_price=80200.0,
        expected_return_pct=0.25,
        up_probability=0.64,
        neutral_threshold_bps=8.0,
        strong_threshold_bps=25.0,
    )

    assert view.direction_code == "UP"
    assert view.direction_label == "收涨（多）"
    assert view.direction_probability == pytest.approx(0.64)
    assert view.expected_return_pct == pytest.approx(0.25)
    assert view.price_change == pytest.approx(200.0)
    assert view.quote_asset == "USDT"
    assert view.strength_label == "强"
    assert view.probability_conflict is False


def test_down_direction_uses_down_probability():
    view = build_direction_view(
        symbol="ETH/USDT",
        base_price=4000.0,
        expected_price=3980.0,
        expected_return_pct=-0.5,
        up_probability=0.38,
        neutral_threshold_bps=8.0,
        strong_threshold_bps=25.0,
    )

    assert view.direction_code == "DOWN"
    assert view.direction_label == "收跌（空）"
    assert view.direction_probability == pytest.approx(0.62)
    assert view.price_change == pytest.approx(-20.0)
    assert view.strength_label == "强"
    assert view.probability_conflict is False


def test_regression_probability_disagreement_is_exposed():
    view = build_direction_view(
        symbol="BTC/USDT",
        base_price=80000.0,
        expected_price=80040.0,
        expected_return_pct=0.05,
        up_probability=0.42,
        neutral_threshold_bps=8.0,
        strong_threshold_bps=25.0,
    )

    assert view.direction_code == "UP"
    assert view.direction_probability == pytest.approx(0.42)
    assert view.probability_conflict is True
    assert view.strength_label == "弱 / 接近震荡"
