"""CUPED variance reduction for the confidence sequence (ARCHITECTURE.md §8.5).

Reference
---------
Deng, A., Xu, Y., Kohavi, R., & Walker, T. (2013). *Improving the sensitivity of
online controlled experiments by utilizing pre-experiment data.* WSDM '13.

CUPED regresses out a pre-experiment covariate ``X`` that is correlated with the
outcome ``Y`` but, measured before randomization, unaffected by the treatment
(Deng et al. 2013, §3):

    Y_cv = Y - theta * X,    Var(Y_cv) = Var(Y) * (1 - rho^2) at theta = Cov(Y, X) / Var(X).

In a randomized experiment ``E[X]`` is the same in both arms, so the difference of
adjusted means is unbiased for the treatment effect for *any* ``theta`` that does
not depend on the outcomes (Deng et al. 2013, §3.2 — the ``E[X]`` centering cancels
in the difference and is omitted here).

Why theta is fixed before the experiment
----------------------------------------
The confidence sequence's guarantee is time-uniform over one running sum of
observations (Howard et al. 2021). Re-estimating ``theta`` from in-experiment data
at every look would make each look's "observations" a different, data-dependent
transform, and the guarantee would no longer apply. So ``theta`` is estimated once,
from pre-period data only — regressing each unit's later pre-window mean on its
earlier one (:func:`estimate_theta`) — and frozen. Any pre-treatment ``theta`` keeps
the inference valid; a better one only buys more variance reduction.

Bounded support
---------------
The empirical-Bernstein CS needs an a-priori support ``[0, c]``. With ``Y`` and
``X`` in ``[0, c]`` (``X`` is the same metric, pre-period), ``Y - theta * X`` lies in
``[-max(theta, 0) * c, c + max(-theta, 0) * c]``. Shifting by ``max(theta, 0) * c``
(the same constant in both arms, so it cancels in the difference) maps it into
``[0, c * (1 + |theta|)]``. ``theta`` is clamped to ``[-1, 1]`` so the support at
most doubles.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from datatool.stats.confidence_sequence import ArmStats

# DECISION: clamp theta to [-1, 1]. For a metric regressed on its own pre-period
# value theta is normally in [0, 1]; the clamp (a fixed function of pre-period
# data, so still valid) caps the support inflation at 2x when pre data are noisy.
THETA_BOUND = 1.0


@dataclass(frozen=True)
class PrePeriodPairs:
    """Unit-level pre-period pairs, aggregated: ``a`` the earlier window's per-unit
    mean, ``b`` the later window's, over units observed in both."""

    n: int
    sum_a: float
    sum_b: float
    sum_aa: float
    sum_ab: float


@dataclass(frozen=True)
class CovariateArm:
    """One arm's outcome ``y`` and covariate ``x`` cross-moments over its observations."""

    n: int
    sum_y: float
    sum_yy: float
    sum_x: float
    sum_xx: float
    sum_xy: float


def estimate_theta(pairs: PrePeriodPairs) -> float:
    """``theta = Cov(a, b) / Var(a)`` from pre-period pairs (Deng et al. 2013, eq. 3).

    Returns 0.0 — no adjustment, plain CS — when there are fewer than two pairs or
    the earlier window has no variance. Clamped to ``[-THETA_BOUND, THETA_BOUND]``.
    """
    if pairs.n < 2:
        return 0.0
    var_a = pairs.sum_aa - pairs.sum_a**2 / pairs.n
    if var_a <= 0:
        return 0.0
    cov_ab = pairs.sum_ab - pairs.sum_a * pairs.sum_b / pairs.n
    return float(np.clip(cov_ab / var_a, -THETA_BOUND, THETA_BOUND))


def adjusted_max_value(theta: float, max_value: float) -> float:
    """Support bound of the shifted adjusted metric: ``c * (1 + |theta|)``."""
    return max_value * (1.0 + abs(theta))


def adjusted_values(
    y: np.ndarray, x: np.ndarray, theta: float | np.ndarray, max_value: float
) -> np.ndarray:
    """Per-observation shifted CUPED values ``y - theta * x + max(theta, 0) * c``.

    Vectorised (``theta`` may broadcast per row); in ``[0, adjusted_max_value]``.
    """
    return y - theta * x + np.maximum(theta, 0.0) * max_value


def cuped_arm(arm: CovariateArm, theta: float, max_value: float) -> ArmStats:
    """The arm's sufficient statistics for the shifted adjusted metric.

    Exactly the aggregate form of :func:`adjusted_values`: with ``z = y - theta*x + s``,
    ``sum z = sum y - theta*sum x + n*s`` and ``sum z^2`` expands from the cross-moments.
    """
    s = max(theta, 0.0) * max_value
    sum_z = arm.sum_y - theta * arm.sum_x + arm.n * s
    sum_zz = (
        arm.sum_yy
        + theta**2 * arm.sum_xx
        + arm.n * s**2
        - 2.0 * theta * arm.sum_xy
        + 2.0 * s * arm.sum_y
        - 2.0 * s * theta * arm.sum_x
    )
    return ArmStats(
        n=arm.n,
        sum=sum_z,
        sum_sq=max(sum_zz, 0.0),  # guard float cancellation; a square sum is >= 0
        max_value=adjusted_max_value(theta, max_value),
    )
