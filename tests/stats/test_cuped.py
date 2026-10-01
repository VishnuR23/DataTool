"""CUPED algebra (ARCHITECTURE.md §8.5; Deng et al. 2013).

The live path works on aggregates, the calibration harness on per-unit arrays; these
tests pin the two to each other and pin the support bound the confidence sequence
relies on. Calibration of the adjusted CS itself is in test_cuped_calibration.py.
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from datatool.stats.cuped import (
    THETA_BOUND,
    CovariateArm,
    PrePeriodPairs,
    adjusted_max_value,
    adjusted_values,
    cuped_arm,
    estimate_theta,
)


def _pairs(a: np.ndarray, b: np.ndarray) -> PrePeriodPairs:
    return PrePeriodPairs(
        n=len(a), sum_a=a.sum(), sum_b=b.sum(), sum_aa=(a * a).sum(), sum_ab=(a * b).sum()
    )


def test_theta_recovers_the_regression_slope_of_later_on_earlier_pre_window():
    rng = np.random.default_rng(1)
    a = rng.random(20_000)
    b = 0.6 * a + 0.2 * rng.random(20_000)
    assert estimate_theta(_pairs(a, b)) == pytest.approx(0.6, abs=0.01)


def test_theta_is_zero_without_usable_pre_period_data():
    """No pairs, one pair, or a constant earlier window -> no adjustment (plain CS)."""
    assert estimate_theta(PrePeriodPairs(0, 0, 0, 0, 0)) == 0.0
    assert estimate_theta(_pairs(np.array([0.5]), np.array([0.7]))) == 0.0
    assert estimate_theta(_pairs(np.full(10, 0.3), np.linspace(0, 1, 10))) == 0.0


def test_theta_is_clamped_so_the_support_at_most_doubles():
    a = np.linspace(0, 0.1, 100)
    assert estimate_theta(_pairs(a, 5 * a)) == THETA_BOUND
    assert estimate_theta(_pairs(a, -5 * a)) == -THETA_BOUND


@settings(max_examples=200, deadline=None)
@given(
    theta=st.floats(-THETA_BOUND, THETA_BOUND),
    c=st.floats(0.5, 200.0),
    seed=st.integers(0, 2**32 - 1),
)
def test_aggregate_adjustment_equals_the_per_unit_adjustment(theta, c, seed):
    rng = np.random.default_rng(seed)
    y, x = c * rng.random(500), c * rng.random(500)
    z = adjusted_values(y, x, theta, c)
    arm = CovariateArm(
        n=500,
        sum_y=y.sum(),
        sum_yy=(y * y).sum(),
        sum_x=x.sum(),
        sum_xx=(x * x).sum(),
        sum_xy=(x * y).sum(),
    )
    stats = cuped_arm(arm, theta, c)
    assert stats.sum == pytest.approx(z.sum(), rel=1e-9, abs=1e-6)
    assert stats.sum_sq == pytest.approx((z * z).sum(), rel=1e-9, abs=1e-6)
    assert stats.max_value == adjusted_max_value(theta, c)


@settings(max_examples=200, deadline=None)
@given(
    theta=st.floats(-THETA_BOUND, THETA_BOUND),
    c=st.floats(0.5, 200.0),
    y_frac=st.floats(0, 1),
    x_frac=st.floats(0, 1),
)
def test_adjusted_values_stay_inside_the_declared_support(theta, c, y_frac, x_frac):
    """The CS's a-priori support: z in [0, c(1 + |theta|)] whenever y, x in [0, c]."""
    z = adjusted_values(np.array(y_frac * c), np.array(x_frac * c), theta, c)
    tol = 1e-9 * c
    assert -tol <= float(z) <= adjusted_max_value(theta, c) + tol


def test_zero_theta_leaves_the_metric_untouched():
    arm = CovariateArm(n=10, sum_y=4.0, sum_yy=4.0, sum_x=3.0, sum_xx=2.0, sum_xy=1.0)
    stats = cuped_arm(arm, 0.0, 1.0)
    assert (stats.n, stats.sum, stats.sum_sq, stats.max_value) == (10, 4.0, 4.0, 1.0)
