from __future__ import annotations

from quant_mm.event_contract_engine import build_event_probability, calculate_market_edge
from quant_mm.forecast_engine import ForecastResult
from quant_mm.institutional_alpha import AlphaResult


def _forecast(up_probability: float = 0.60, expected_return_pct: float = 0.20) -> ForecastResult:
    return ForecastResult(
        timeframe="15m",
        as_of="2026-10-25T12:00:00+00:00",
        current_price=80000.0,
        expected_return_pct=expected_return_pct,
        expected_price=80160.0,
        lower_price=79700.0,
        upper_price=80500.0,
        up_probability=up_probability,
        confidence=0.70,
        trend="UP",
        research_signal="LONG_BIAS",
        market_regime="TREND_UP",
        realized_volatility_pct=0.30,
        validation_mae_pct=0.12,
        validation_direction_accuracy=0.58,
        model_agreement=0.75,
        model_predictions_pct={"linear": 0.18},
        model_weights={"linear": 1.0},
        validation_model_weights={"linear": 1.0},
        weighting_mode="validation_only",
        training_rows=1500,
        recency_half_life_bars=0.0,
        manual_model_multipliers={},
    )


def _alpha(score: float) -> AlphaResult:
    return AlphaResult(
        composite_score=score,
        risk_multiplier=1.0,
        consensus_score=score,
        consensus_signal="LONG_BIAS" if score > 0 else "SHORT_BIAS",
        consensus_confidence=0.80,
        factor_scores={"momentum": score},
        factor_weights={"momentum": 1.0},
        raw_context={},
        data_quality=0.90,
        source="test",
    )


def test_positive_alpha_can_raise_but_not_override_probability_unbounded():
    base = build_event_probability(_forecast(), None, event_reference_price=80000.0)
    boosted = build_event_probability(_forecast(), _alpha(0.8), event_reference_price=80000.0)

    assert boosted.final_up_probability > base.final_up_probability
    assert 0.0 < boosted.final_up_probability < 1.0
    assert boosted.alpha_adjustment_logit <= 0.80


def test_event_reference_price_changes_regression_leg():
    low_reference = build_event_probability(_forecast(), None, event_reference_price=79900.0)
    high_reference = build_event_probability(_forecast(), None, event_reference_price=80200.0)
    assert low_reference.regression_implied_up_probability > high_reference.regression_implied_up_probability


def test_market_edge_uses_binary_settlement_ev():
    edge = calculate_market_edge(0.70, up_price=0.55, down_price=0.47)
    assert round(edge.up_ev_per_share, 6) == 0.15
    assert round(edge.down_ev_per_share, 6) == -0.17
    assert edge.best_side == "UP"
    assert edge.up_roi_pct > 27.0
