"""The CUPED-adjusted confidence sequence still controls type-I error (§8.5, §8.6).

This is the gate ``enable_cuped`` waited on. Under H0 with a pre-period covariate
correlated with the outcome, ``theta`` is estimated per run from the *same units'*
pre-period data (exactly as in production — it is frozen before the experiment,
never refit on outcomes), the adjusted values come from the real
``adjusted_values``, and the real ``poly_stitching_bound`` decides, at every step,
whether the difference CS excludes 0. Realized type-I error must sit at or below
alpha + the same Monte Carlo slack as the unadjusted calibration.

Data model per unit u: latent rate p_u ~ Beta(0.5, 2); two pre-period windows,
each the mean of 50 Bernoulli(p_u) events (earlier ``a_u``, later ``b_u``); the
in-experiment outcome y_u ~ Bernoulli(p_u). The covariate is x_u = b_u. Both arms
share the law (H0). rho^2 between y and x is ~0.27 — a realistic CUPED regime.
"""

from __future__ import annotations

import numpy as np

from datatool.stats.confidence_sequence import ArmStats, confidence_sequence_diff
from datatool.stats.cuped import (
    THETA_BOUND,
    CovariateArm,
    PrePeriodPairs,
    adjusted_max_value,
    adjusted_values,
    cuped_arm,
    estimate_theta,
)

from .test_cs_calibration import ALPHA, SLACK, _ever_excludes_zero

N_RUNS = 10_000
MAX_N = 2_000  # units per arm per run
PRE_EVENTS = 50


def _units(rng, shape):
    p = rng.beta(0.5, 2.0, size=shape)
    a = rng.binomial(PRE_EVENTS, p) / PRE_EVENTS
    b = rng.binomial(PRE_EVENTS, p) / PRE_EVENTS
    y = (rng.random(shape) < p).astype(float)
    return a, b, y


def _theta_per_run(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Vectorised estimate_theta over rows (pinned to the scalar one below)."""
    n = a.shape[1]
    var_a = (a * a).sum(axis=1) - a.sum(axis=1) ** 2 / n
    cov = (a * b).sum(axis=1) - a.sum(axis=1) * b.sum(axis=1) / n
    theta = np.where(var_a > 0, cov / np.where(var_a > 0, var_a, 1.0), 0.0)
    return np.clip(theta, -THETA_BOUND, THETA_BOUND)


def _cuped_type_i_error(seed: int) -> float:
    rng = np.random.default_rng(seed)
    rejected, done, batch = 0, 0, 500
    while done < N_RUNS:
        size = min(batch, N_RUNS - done)
        a, b, y = _units(rng, (size, 2 * MAX_N))  # both arms' units, pre-assignment
        theta = _theta_per_run(a, b)[:, None]
        z = adjusted_values(y, b, theta, 1.0)
        c = adjusted_max_value(theta, 1.0)  # per-run support c * (1 + |theta|), c = 1
        excluded = _ever_excludes_zero(z[:, :MAX_N], z[:, MAX_N:], ALPHA, v_min=1.0, c=c)
        rejected += int(excluded.sum())
        done += size
    return rejected / N_RUNS


def test_cuped_adjusted_cs_type_i_error_at_or_below_alpha():
    """Type-I error <= alpha for the CUPED-adjusted CS under H0 (seed 46).

    Verifies: freezing a pre-period theta and shifting into [0, c(1+|theta|)] keeps
    the time-uniform guarantee of Howard et al. (2021) for the adjusted metric.
    Failure means: the adjustment leaks outcome information into the boundary, and
    enable_cuped would ship false-positive wins.
    """
    realized = _cuped_type_i_error(seed=46)
    assert realized <= ALPHA + SLACK, f"realized type-I error {realized} exceeds {ALPHA + SLACK}"


def test_vectorised_theta_matches_estimate_theta():
    rng = np.random.default_rng(7)
    a, b, _ = _units(rng, (3, 4_000))
    for row in range(3):
        pairs = PrePeriodPairs(
            n=a.shape[1],
            sum_a=a[row].sum(),
            sum_b=b[row].sum(),
            sum_aa=(a[row] ** 2).sum(),
            sum_ab=(a[row] * b[row]).sum(),
        )
        assert _theta_per_run(a, b)[row] == np.float64(estimate_theta(pairs))


def test_cuped_narrows_the_cs_when_the_covariate_is_informative():
    """Variance reduction (Deng et al. 2013): same data, tighter interval (seed 48).

    Why it matters: CUPED earns its place only if it shortens experiments; with
    rho^2 ~ 0.27 the adjusted half-width should be clearly smaller despite the
    wider support bound.
    """
    rng = np.random.default_rng(48)
    n = 50_000
    a, b, y = _units(rng, (1, 2 * n))
    a, b, y = a[0], b[0], y[0]
    theta = estimate_theta(PrePeriodPairs(len(a), a.sum(), b.sum(), (a * a).sum(), (a * b).sum()))

    def raw(ys):
        return ArmStats(n=len(ys), sum=ys.sum(), sum_sq=(ys * ys).sum(), max_value=1.0)

    def adjusted(ys, xs):
        moments = CovariateArm(
            n=len(ys),
            sum_y=ys.sum(),
            sum_yy=(ys * ys).sum(),
            sum_x=xs.sum(),
            sum_xx=(xs * xs).sum(),
            sum_xy=(xs * ys).sum(),
        )
        return cuped_arm(moments, theta, 1.0)

    plain = confidence_sequence_diff(raw(y[:n]), raw(y[n:]), alpha=ALPHA)
    cuped = confidence_sequence_diff(adjusted(y[:n], b[:n]), adjusted(y[n:], b[n:]), alpha=ALPHA)
    assert (cuped.upper - cuped.lower) < 0.9 * (plain.upper - plain.lower)
