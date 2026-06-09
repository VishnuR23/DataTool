"""Direct tests of the confidence-sequence math (not just the wrapper).

These pin the *exact* arithmetic of the poly-stitching boundary (Howard et al.
2021) and the structural properties any valid uniform boundary must have, so a
typo in a constant or a swapped term is caught immediately — independently of the
Monte Carlo calibration.
"""

from __future__ import annotations

from math import log, sqrt

import pytest
from scipy.special import zeta

from datatool.core.exceptions import InsufficientDataError, StatisticsError
from datatool.stats.confidence_sequence import (
    ArmStats,
    confidence_sequence_diff,
    poly_stitching_bound,
)


def _reference_bound(v, alpha, v_min, c, s=1.4, eta=2.0):
    """Independent closed-form recomputation of the poly-stitching boundary.

    Mirrors Howard et al. (2021) term by term: any divergence from the module's
    implementation indicates a transcription error in a constant or term.
    """
    k1 = (eta**0.25 + eta**-0.25) / sqrt(2.0)
    k2 = (sqrt(eta) + 1.0) / 2.0
    big_a = log(zeta(s) / (log(eta) ** s))
    use_v = max(v, v_min)
    ell = s * log(log(eta * use_v / v_min)) + big_a + log(1.0 / alpha)
    term2 = k2 * c * ell
    return sqrt(k1 * k1 * use_v * ell + term2 * term2) + term2


def test_poly_stitching_bound_matches_closed_form_exactly():
    """The implemented boundary equals the paper's closed form to machine precision.

    Verifies the exact constants k1, k2, A and the assembled width formula.
    Failure means a constant or term was transcribed wrong — the boundary would be
    silently mis-sized and coverage would be off.
    """
    for v, alpha, v_min, c in [
        (100.0, 0.0125, 1.0, 1.0),
        (5.0, 0.05, 1.0, 1.0),
        (1000.0, 0.001, 10.0, 0.5),
        (0.0, 0.025, 2.0, 1.0),  # v below v_min -> uses v_min
    ]:
        got = float(poly_stitching_bound(v, alpha, v_min=v_min, c=c))
        assert got == pytest.approx(_reference_bound(v, alpha, v_min, c), rel=1e-12)


def test_boundary_grows_with_variance_process():
    """u(v) is increasing in the variance process v.

    A larger accumulated variance must widen the boundary; if it shrank, high-
    variance arms would be declared significant too easily.
    """
    vals = [poly_stitching_bound(v, 0.0125, v_min=1.0, c=1.0) for v in (1, 10, 100, 1000)]
    assert all(b < a for b, a in zip(vals, vals[1:], strict=False))


def test_boundary_widens_as_alpha_shrinks():
    """A stricter alpha yields a wider boundary.

    More stringent error control must demand more evidence; an inverted relation
    would make stricter tests *easier* to reject, the opposite of correct.
    """
    strict = poly_stitching_bound(100.0, 0.001, v_min=1.0, c=1.0)
    loose = poly_stitching_bound(100.0, 0.05, v_min=1.0, c=1.0)
    assert strict > loose


def test_sub_gaussian_limit_when_scale_is_zero():
    """With scale c=0 the boundary reduces to the pure sub-Gaussian k1*sqrt(v*ell).

    Verifies the sub-gamma correction term vanishes correctly at c=0, isolating
    the leading variance term.
    """
    v, alpha, v_min = 100.0, 0.0125, 1.0
    got = float(poly_stitching_bound(v, alpha, v_min=v_min, c=0.0))
    k1 = (2**0.25 + 2**-0.25) / sqrt(2.0)
    big_a = log(zeta(1.4) / (log(2.0) ** 1.4))
    ell = 1.4 * log(log(2.0 * v / v_min)) + big_a + log(1.0 / alpha)
    assert got == pytest.approx(k1 * sqrt(v * ell), rel=1e-12)


def test_difference_point_estimate_is_mean_difference():
    """The CS point estimate is exactly mean(treatment) - mean(control)."""
    cs = confidence_sequence_diff(
        ArmStats(n=200, sum=20.0, sum_sq=20.0, max_value=1.0),
        ArmStats(n=200, sum=30.0, sum_sq=30.0, max_value=1.0),
    )
    assert cs.point_estimate == pytest.approx(30.0 / 200 - 20.0 / 200)
    # Interval is symmetric about the point estimate.
    assert cs.upper - cs.point_estimate == pytest.approx(cs.point_estimate - cs.lower)


def test_smaller_alpha_gives_wider_difference_interval():
    """Tightening alpha widens the difference CS (more evidence required)."""
    args = (
        ArmStats(n=500, sum=50.0, sum_sq=50.0, max_value=1.0),
        ArmStats(n=500, sum=70.0, sum_sq=70.0, max_value=1.0),
    )
    wide = confidence_sequence_diff(*args, alpha=0.01)
    narrow = confidence_sequence_diff(*args, alpha=0.10)
    assert (wide.upper - wide.lower) > (narrow.upper - narrow.lower)


def test_empty_arm_raises_insufficient_data():
    """An arm with no observations raises rather than returning a bogus number."""
    with pytest.raises(InsufficientDataError):
        confidence_sequence_diff(
            ArmStats(n=0, sum=0.0, sum_sq=0.0, max_value=1.0),
            ArmStats(n=10, sum=5.0, sum_sq=5.0, max_value=1.0),
        )


def test_non_positive_support_bound_raises():
    """A non-positive support bound (the sub-gamma scale c) is invalid input."""
    with pytest.raises(StatisticsError):
        confidence_sequence_diff(
            ArmStats(n=10, sum=5.0, sum_sq=5.0, max_value=0.0),
            ArmStats(n=10, sum=5.0, sum_sq=5.0, max_value=1.0),
        )


def test_single_observation_yields_finite_interval_covering_zero():
    """At n=1 (zero observed variance) the CS is finite and very wide, covering 0.

    The boundary falls back to v_min, producing a deliberately huge interval so the
    controller cannot stop on a single observation.
    """
    cs = confidence_sequence_diff(
        ArmStats(n=1, sum=1.0, sum_sq=1.0, max_value=1.0),
        ArmStats(n=1, sum=0.0, sum_sq=0.0, max_value=1.0),
    )
    assert cs.lower < 0 < cs.upper
    assert cs.upper - cs.lower < float("inf")
