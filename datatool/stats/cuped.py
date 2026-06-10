"""CUPED variance reduction (deferred stub — ARCHITECTURE.md §8.5).

Reference
---------
Deng, A., Xu, Y., Kohavi, R., & Walker, T. (2013). *Improving the sensitivity of
online controlled experiments by utilizing pre-experiment data.* WSDM '13.

CUPED (Controlled-experiment Using Pre-Experiment Data) reduces the variance of a
metric by regressing out a pre-experiment covariate that is correlated with the
outcome but, being measured *before* randomization, is unaffected by the
treatment. The adjusted metric

    Y_cuped = Y - theta * (X - E[X]),   theta = Cov(Y, X) / Var(X),

has the same expectation as Y but smaller variance, so feeding ``Y_cuped`` into the
confidence sequence tightens the interval and shortens experiments — most for
low-variance metrics with strong pre-experiment correlation.

Status
------
Feature-flagged **off** by default (``contract.statistics.enable_cuped``) and not
on the MVP critical path, so it is intentionally unimplemented for now. Building it
out is tracked for a later step; doing so must come with its own calibration test
(adjusted CS still controls type-I error) before it can be enabled.
"""

from __future__ import annotations

import numpy as np


def apply_cuped(
    metric_values: np.ndarray,
    pre_experiment_covariate: np.ndarray,
) -> np.ndarray:
    """Return CUPED-adjusted per-unit metric values (Deng et al. 2013).

    ``metric_values`` and ``pre_experiment_covariate`` are aligned per-unit arrays;
    the return value is the variance-reduced metric to feed into the confidence
    sequence in place of the raw metric.

    Not implemented yet — see the module docstring.
    """
    raise NotImplementedError(
        "CUPED is deferred (ARCHITECTURE.md §8.5); enable_cuped must stay false "
        "until this is implemented and its own calibration test passes."
    )
