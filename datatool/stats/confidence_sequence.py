"""Empirical-Bernstein time-uniform confidence sequences.

Reference
---------
Howard, S. R., Ramdas, A., McAuliffe, J., & Sekhon, J. (2021).
*Time-uniform, nonparametric, nonasymptotic confidence sequences.*
Annals of Statistics, 49(2), 1055-1080. arXiv:1810.08240.

What a confidence sequence buys us
----------------------------------
A confidence sequence (CS) is a sequence of intervals ``(L_t, U_t)`` that
covers the true parameter *simultaneously for all* ``t`` with probability at
least ``1 - alpha``::

    P( exists t >= 1 : theta not in (L_t, U_t) )  <=  alpha .

That time-uniform guarantee is exactly what an autonomous controller needs: it
peeks at the data on every decision cycle and stops the moment the interval
excludes the null, with the type-I error still controlled at ``alpha``. A
fixed-horizon confidence interval does *not* give this — repeatedly testing it
inflates the false-positive rate, which is the classic "peeking" footgun.

Construction used here
----------------------
We build a one-dimensional CS for each arm's mean from the **polynomial-stitching
uniform boundary** (Howard et al. 2021, the stitched boundary of their §3.6,
Theorem 1), instantiated for a *sub-gamma* process whose intrinsic time is the
empirical variance process — this is the empirical-Bernstein instance (their
§3.7 / Theorem 4). The boundary matches the authors' reference ``confseq``
library function ``poly_stitching_bound``; every constant below is tied to that
construction. The brief's "polynomial mixture, rho = 1.4" is this boundary with
the stitching exponent ``s = 1.4``.

The CS for the *difference* of the two arm means is then formed by a Bonferroni
split across the two independent per-arm CSs (see :func:`confidence_sequence_diff`).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.special import zeta

from datatool.core.exceptions import InsufficientDataError, StatisticsError

# Tuning constants for the poly-stitching boundary (Howard et al. 2021).
#   s = 1.4 : the polynomial stitching exponent (the brief's "rho = 1.4"); the
#             boundary grows at the law-of-iterated-logarithm rate with this power.
#   eta = 2 : geometric spacing of the stitched epochs.
# These are the confseq defaults and are valid for any choice with s > 1, eta > 1.
_DEFAULT_S = 1.4
_DEFAULT_ETA = 2.0

# Default intrinsic-time scale at which the boundary is tightest. Validity of the
# boundary holds for *any* v_min > 0 (Theorem 1); v_min only trades early-time
# tightness against late-time tightness. 1.0 keeps the boundary very wide while
# an arm has near-zero observed variance, which suppresses spurious early stops.
_DEFAULT_V_MIN = 1.0


@dataclass
class CSBounds:
    """A confidence-sequence interval for the treatment-minus-control difference."""

    lower: float
    upper: float
    point_estimate: float


@dataclass
class ArmStats:
    """Running sufficient statistics for one arm.

    ``max_value`` is the *a priori* upper bound on the support (e.g. 1.0 for a
    rate in [0, 1]), not the largest value observed so far — it is the ``c`` that
    parameterises the sub-gamma scale of the empirical-Bernstein boundary.
    """

    n: int
    sum: float
    sum_sq: float
    max_value: float

    @property
    def mean(self) -> float:
        return self.sum / self.n if self.n > 0 else 0.0

    @property
    def variance(self) -> float:
        # Unbiased sample variance. Reported for callers/inspection; the CS itself
        # uses the (n-1)*variance "variance process" form computed inline below.
        if self.n < 2:
            return 0.0
        return (self.sum_sq - self.n * self.mean**2) / (self.n - 1)


def poly_stitching_bound(
    v: np.ndarray | float,
    alpha: float,
    v_min: float = _DEFAULT_V_MIN,
    c: float = 0.0,
    s: float = _DEFAULT_S,
    eta: float = _DEFAULT_ETA,
) -> np.ndarray | float:
    """Polynomial-stitching uniform boundary ``u(v)`` of Howard et al. (2021).

    Returns ``u`` such that, for a sub-gamma process ``S_t`` with variance process
    ``V_t`` and scale ``c``,

        P( exists t : S_t >= u(V_t) )  <=  alpha .

    This is the stitched boundary of their §3.6 (Theorem 1); it reproduces the
    ``confseq`` reference implementation ``PolyStitchingBound``. ``v`` may be a
    scalar or a NumPy array (vectorised over many evaluation points).

    Parameters mirror the paper: ``v_min`` is the intrinsic time at which the
    boundary is optimised, ``c`` the sub-gamma scale, ``s`` the stitching exponent,
    ``eta`` the epoch spacing.
    """
    if v_min <= 0:
        raise StatisticsError(f"v_min must be positive, got {v_min}")
    if not (0 < alpha < 1):
        raise StatisticsError(f"alpha must be in (0, 1), got {alpha}")

    # Constants k1, k2 (Howard et al. 2021, poly-stitching construction).
    k1 = (eta**0.25 + eta**-0.25) / np.sqrt(2.0)  # k1 = (eta^{1/4} + eta^{-1/4}) / sqrt(2)
    k2 = (np.sqrt(eta) + 1.0) / 2.0  # k2 = (sqrt(eta) + 1) / 2
    # A = log( zeta(s) / log(eta)^s ), the stitched union-bound normaliser.
    big_a = np.log(zeta(s) / (np.log(eta) ** s))

    use_v = np.maximum(v, v_min)  # boundary evaluated at max(v, v_min)
    # ell(v) = s*log(log(eta*v/v_min)) + A + log(1/alpha)   (the stitched "radius").
    ell = s * np.log(np.log(eta * use_v / v_min)) + big_a + np.log(1.0 / alpha)
    term2 = k2 * c * ell  # sub-gamma (linear-in-ell) correction term
    # u(v) = sqrt( k1^2 * v * ell + term2^2 ) + term2.
    return np.sqrt(k1 * k1 * use_v * ell + term2 * term2) + term2


def _arm_half_width(arm: ArmStats, alpha_side: float, v_min: float) -> float:
    """Half-width of the one-arm empirical-Bernstein CS for that arm's mean.

    The boundary bounds the centred *sum* process ``S_t = sum_i (X_i - mu) = n*(mean - mu)``,
    so the half-width on the *mean* is ``u(V_t) / n``. The variance process is the
    empirical sum of squared deviations ``V_t = sum_i (X_i - mean)^2 = sum_sq - n*mean^2``.
    """
    n = arm.n
    mean = arm.sum / n
    # V_t = sum of squared deviations = (n-1)*sample_variance; clamp tiny negative
    # values from floating-point cancellation to zero.
    variance_process = max(arm.sum_sq - n * mean * mean, 0.0)
    u = poly_stitching_bound(variance_process, alpha_side, v_min=v_min, c=arm.max_value)
    return float(u) / n


def confidence_sequence_diff(
    control: ArmStats,
    treatment: ArmStats,
    alpha: float = 0.05,
    *,
    v_min: float = _DEFAULT_V_MIN,
) -> CSBounds:
    """Time-uniform CS for ``mean(treatment) - mean(control)``.

    Builds a per-arm empirical-Bernstein CS (Howard et al. 2021) for each mean and
    combines them for the difference. Coverage budget: the difference CS excludes
    its true value only if one of *four* one-sided boundary-crossing events occurs
    (control or treatment, low or high side), so each one-sided boundary is run at
    ``alpha / 4`` (a Bonferroni split). This makes

        P( exists t : (mean_T - mean_C) not in (L_t, U_t) )  <=  alpha

    time-uniformly. The split is deliberately conservative — see the module note;
    a tighter direct-difference martingale is possible future work and would only
    *narrow* the interval, never invalidate it.

    Raises :class:`InsufficientDataError` if either arm has no observations and
    :class:`StatisticsError` for structurally invalid stats (negative counts/sums
    of squares, a non-positive support bound).
    """
    for label, arm in (("control", control), ("treatment", treatment)):
        if arm.n < 1:
            raise InsufficientDataError(f"{label} arm has no observations (n={arm.n})")
        if arm.n < 0 or arm.sum_sq < 0:
            raise StatisticsError(f"{label} arm has invalid stats: {arm}")
        if arm.max_value <= 0:
            raise StatisticsError(
                f"{label} arm max_value (support bound) must be positive, got {arm.max_value}"
            )

    # Four one-sided crossing events over the two arms -> alpha/4 per boundary.
    alpha_side = alpha / 4.0

    hw_control = _arm_half_width(control, alpha_side, v_min)
    hw_treatment = _arm_half_width(treatment, alpha_side, v_min)

    point = treatment.mean - control.mean
    half_width = hw_control + hw_treatment
    return CSBounds(lower=point - half_width, upper=point + half_width, point_estimate=point)
