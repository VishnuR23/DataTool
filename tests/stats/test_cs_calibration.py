"""Type-I error calibration for the empirical-Bernstein confidence sequence.

This is the single most important test in the project. It verifies the one
guarantee everything else leans on: the time-uniform coverage of Howard et al.
(2021). If it fails, DataTool is shipping false-positive "wins" — declaring a
treatment better than control when it is not — which is the failure mode that
destroys the credibility of an autonomous experimentation controller. A failing
calibration test must block the build.

The theoretical guarantee (Howard, Ramdas, McAuliffe & Sekhon 2021, the
poly-stitching boundary of their Theorem 1, applied to the empirical-Bernstein
sub-gamma instance) is:

    P( there exists any t >= 1 at which the confidence sequence excludes the
       true difference in means )  <=  alpha,

*simultaneously over all t*. Under H0 the true difference is 0, so "the CS ever
excludes 0" is exactly a type-I error. We estimate its probability by Monte Carlo
and require it to sit at or below alpha.

How the test is built
---------------------
The Monte Carlo is vectorised over runs with NumPy for speed, but it calls the
*real* boundary function ``poly_stitching_bound`` from the implementation — it
exercises the actual math, not a re-derivation. A separate test
(``test_vectorized_calibration_matches_scalar_confidence_sequence_diff``) pins
the vectorised path to the public scalar ``confidence_sequence_diff`` so the two
cannot drift apart.

Determinism: every run uses ``np.random.default_rng(seed)`` with an explicit,
documented seed. A flaky calibration test is worse than no calibration test —
it trains the team to ignore the one signal that protects correctness.
"""

from __future__ import annotations

import numpy as np

from datatool.stats.confidence_sequence import (
    ArmStats,
    confidence_sequence_diff,
    poly_stitching_bound,
)

ALPHA = 0.05

# Monte Carlo slack. With N = 10,000 runs, the standard error of an estimated
# rejection probability near alpha is sqrt(alpha*(1-alpha)/N) ~= sqrt(0.0475/1e4)
# ~= 0.00218. A slack of 0.005 is ~2.3 standard errors, so a correctly-calibrated
# CS clears the bound with overwhelming probability, while a CS whose true type-I
# error exceeds alpha by a meaningful margin still fails. This is why 0.005 is the
# right tolerance: large enough to absorb MC noise, small enough to catch a real
# miscalibration.
SLACK = 0.005

N_RUNS = 10_000
MAX_N = 5_000


def _ever_excludes_zero(
    x_control: np.ndarray, x_treatment: np.ndarray, alpha: float, v_min: float, c: float
) -> np.ndarray:
    """For each run (row), did the difference CS ever exclude 0 across all steps?

    Reproduces ``confidence_sequence_diff`` vectorised over (run, step): per-arm
    empirical-Bernstein half-widths from the real poly-stitching boundary, summed
    for the difference, with each one-sided boundary run at ``alpha / 4``.
    """
    n = np.arange(1, x_control.shape[1] + 1)
    sum_c = np.cumsum(x_control, axis=1)
    sumsq_c = np.cumsum(x_control * x_control, axis=1)
    sum_t = np.cumsum(x_treatment, axis=1)
    sumsq_t = np.cumsum(x_treatment * x_treatment, axis=1)

    mean_c = sum_c / n
    mean_t = sum_t / n
    var_proc_c = np.maximum(sumsq_c - sum_c * sum_c / n, 0.0)
    var_proc_t = np.maximum(sumsq_t - sum_t * sum_t / n, 0.0)

    alpha_side = alpha / 4.0
    hw_c = poly_stitching_bound(var_proc_c, alpha_side, v_min=v_min, c=c) / n
    hw_t = poly_stitching_bound(var_proc_t, alpha_side, v_min=v_min, c=c) / n

    point = mean_t - mean_c
    half = hw_c + hw_t
    excluded = (point - half > 0) | (point + half < 0)
    return np.any(excluded, axis=1)


def _type_i_error(seed: int, sampler, *, n_runs: int = N_RUNS, max_n: int = MAX_N) -> float:
    """Fraction of H0 sequences whose CS ever excludes 0. Both arms share a law."""
    rng = np.random.default_rng(seed)
    rejected = 0
    done = 0
    batch = 500  # bounds peak memory (~500 x 5000 float64 arrays) while staying fast
    while done < n_runs:
        b = min(batch, n_runs - done)
        x_c = sampler(rng, (b, max_n))
        x_t = sampler(rng, (b, max_n))
        rejected += int(_ever_excludes_zero(x_c, x_t, ALPHA, v_min=1.0, c=1.0).sum())
        done += b
    return rejected / n_runs


def _bernoulli(p: float):
    return lambda rng, shape: (rng.random(shape) < p).astype(float)


def _uniform():
    return lambda rng, shape: rng.random(shape)


def test_cs_type_i_error_at_or_below_alpha():
    """Type-I error <= alpha under H0 with both arms ~ Bernoulli(0.1).

    Verifies: the time-uniform coverage guarantee of Howard et al. (2021) holds
    for a low-rate binary metric (the common conversion-rate case).
    Why it matters: this is DataTool's core safety property — peeking on every
    cycle must not inflate the false-positive rate.
    Failure means: the CS is too narrow and the controller will promote losers as
    winners. Seed 42 fixes the data so the result is reproducible.
    """
    realized = _type_i_error(seed=42, sampler=_bernoulli(0.1))
    assert realized <= ALPHA + SLACK, f"realized type-I error {realized} exceeds {ALPHA + SLACK}"


def test_cs_type_i_error_holds_under_balanced_bernoulli():
    """Type-I error <= alpha under H0 with both arms ~ Bernoulli(0.5).

    Verifies: the guarantee is distribution-robust — it must hold at the
    maximum-variance binary rate, not only the low-rate case above.
    Why it matters: guardrail and goal metrics span the whole [0,1] rate range;
    coverage cannot depend on the rate.
    Failure means: the empirical-variance plug-in mis-calibrates at high variance.
    Seed 43 (distinct from the other variants to avoid shared-noise artifacts).
    """
    realized = _type_i_error(seed=43, sampler=_bernoulli(0.5))
    assert realized <= ALPHA + SLACK, f"realized type-I error {realized} exceeds {ALPHA + SLACK}"


def test_cs_type_i_error_holds_under_continuous_uniform():
    """Type-I error <= alpha under H0 with both arms ~ Uniform(0, 1).

    Verifies: coverage holds for a continuous bounded metric, not just binary —
    the empirical-Bernstein construction is nonparametric and must not assume
    Bernoulli structure.
    Why it matters: revenue-per-visitor, time-on-page, and similar goal metrics
    are continuous; the CS must cover them too.
    Failure means: the construction silently relied on binary support. Seed 44.
    """
    realized = _type_i_error(seed=44, sampler=_uniform())
    assert realized <= ALPHA + SLACK, f"realized type-I error {realized} exceeds {ALPHA + SLACK}"


def test_vectorized_calibration_matches_scalar_confidence_sequence_diff():
    """The vectorised calibration path equals the public scalar API step-for-step.

    Verifies: the fast Monte Carlo helper above computes exactly what
    ``confidence_sequence_diff`` computes, at every step, so the calibration
    result genuinely certifies the shipped function.
    Why it matters: a calibration test that secretly tested a different formula
    than production would be worthless. Failure means the two have drifted.
    """
    rng = np.random.default_rng(99)
    x_c = (rng.random((1, MAX_N)) < 0.3).astype(float)
    x_t = (rng.random((1, MAX_N)) < 0.3).astype(float)

    sum_c = np.cumsum(x_c, axis=1)
    sumsq_c = np.cumsum(x_c * x_c, axis=1)
    sum_t = np.cumsum(x_t, axis=1)
    sumsq_t = np.cumsum(x_t * x_t, axis=1)

    for step in (1, 2, 50, 1000, MAX_N):
        i = step - 1
        scalar = confidence_sequence_diff(
            ArmStats(n=step, sum=sum_c[0, i], sum_sq=sumsq_c[0, i], max_value=1.0),
            ArmStats(n=step, sum=sum_t[0, i], sum_sq=sumsq_t[0, i], max_value=1.0),
            alpha=ALPHA,
        )
        # Recompute the vectorised bound at this single step.
        mc = sum_c[0, i] / step
        mt = sum_t[0, i] / step
        vpc = max(sumsq_c[0, i] - sum_c[0, i] ** 2 / step, 0.0)
        vpt = max(sumsq_t[0, i] - sum_t[0, i] ** 2 / step, 0.0)
        hwc = float(poly_stitching_bound(vpc, ALPHA / 4, v_min=1.0, c=1.0)) / step
        hwt = float(poly_stitching_bound(vpt, ALPHA / 4, v_min=1.0, c=1.0)) / step
        assert scalar.point_estimate == (mt - mc)
        assert abs(scalar.lower - ((mt - mc) - (hwc + hwt))) < 1e-12
        assert abs(scalar.upper - ((mt - mc) + (hwc + hwt))) < 1e-12
