from __future__ import annotations

import numpy as np
import pytest

from quant_mm.forecast_engine import _apply_manual_multipliers, _recency_weights


def test_recency_half_life_weights_newer_samples_more():
    weights = _recency_weights(11, half_life_bars=5)
    assert weights is not None
    assert weights[-1] > weights[0]
    # The newest sample is about twice the weight of a sample five bars older.
    ratio = float(weights[-1] / weights[-6])
    assert ratio == pytest.approx(2.0, rel=1e-6)
    assert float(np.mean(weights)) == pytest.approx(1.0, rel=1e-9)


def test_recency_weight_zero_disables_sample_weighting():
    assert _recency_weights(100, 0) is None


def test_manual_multiplier_moves_weight_but_is_bounded():
    base = {"linear": 0.50, "ridge": 0.50}
    adjusted, changed, bounded = _apply_manual_multipliers(
        base,
        {"linear": 10.0, "ridge": 0.01},
    )

    assert changed is True
    assert bounded["linear"] == 2.0
    assert bounded["ridge"] == 0.25
    assert adjusted["linear"] > base["linear"]
    assert adjusted["ridge"] < base["ridge"]
    assert sum(adjusted.values()) == pytest.approx(1.0)


def test_all_one_manual_multipliers_do_not_change_mode():
    base = {"linear": 0.2, "ridge": 0.3, "hist_gb": 0.5}
    adjusted, changed, bounded = _apply_manual_multipliers(
        base,
        {name: 1.0 for name in base},
    )
    assert changed is False
    assert adjusted == pytest.approx(base)
    assert all(value == 1.0 for value in bounded.values())
