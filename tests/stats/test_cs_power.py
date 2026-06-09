"""Power (detection) regression guard for the confidence sequence.

This is *not* a correctness gate — coverage (the calibration test) is what makes
the CS correct. This test guards the opposite failure: a CS so wide it never
detects a real effect would trivially pass calibration yet be useless. It pins
that a genuine win is detected, at a documented sample size, most of the time.

Scenario and back-of-envelope sample size
------------------------------------------
Control ~ Bernoulli(0.50), treatment ~ Bernoulli(0.60): a true effect of
delta = 0.10, which is 2x a minimum detectable effect of MDE = 0.05.

A fixed-horizon two-sample test would need, per arm,

    n ~= 2 * sigma^2 * (z_{alpha/2} + z_beta)^2 / delta^2
       = 2 * 0.25 * (1.96 + 0.84)^2 / 0.10^2
       ~= 392 observations/arm

for 80% power at alpha = 0.05. A time-uniform confidence sequence pays an
overhead for being valid at *every* step (not one fixed horizon), and our
difference CS pays a further Bonferroni factor for splitting alpha across two
arms. Empirically the CS detects this effect at a median of ~2,100 obs/arm
(~5x the fixed-horizon sample) and within n_max = 4,000 obs/arm in >99% of runs.
We assert detection in >= 80% of runs by n_max — a wide margin below the observed
~0.998, chosen so the test is a stable regression guard rather than a knife-edge.

A regression that materially widened the CS (e.g. a doubled boundary) would drop
detection below 80% and fail here. Determinism via np.random.default_rng(7).
"""

from __future__ import annotations

import numpy as np
import pytest

from datatool.stats.confidence_sequence import poly_stitching_bound

ALPHA = 0.05
MDE = 0.05
DELTA = 2 * MDE  # true effect = 2x MDE
N_MAX = 4_000
N_RUNS = 1_000
MIN_POWER = 0.80


def _detection_rate(seed: int) -> float:
    """Fraction of runs whose one-sided lower CS bound exceeds 0 by step N_MAX."""
    rng = np.random.default_rng(seed)
    n = np.arange(1, N_MAX + 1)
    detected = 0
    done = 0
    batch = 500
    while done < N_RUNS:
        b = min(batch, N_RUNS - done)
        x_c = (rng.random((b, N_MAX)) < 0.50).astype(float)
        x_t = (rng.random((b, N_MAX)) < 0.50 + DELTA).astype(float)

        sum_c = np.cumsum(x_c, axis=1)
        sumsq_c = np.cumsum(x_c * x_c, axis=1)
        sum_t = np.cumsum(x_t, axis=1)
        sumsq_t = np.cumsum(x_t * x_t, axis=1)

        mean_c = sum_c / n
        mean_t = sum_t / n
        vpc = np.maximum(sumsq_c - sum_c * sum_c / n, 0.0)
        vpt = np.maximum(sumsq_t - sum_t * sum_t / n, 0.0)

        hw_c = poly_stitching_bound(vpc, ALPHA / 4, v_min=1.0, c=1.0) / n
        hw_t = poly_stitching_bound(vpt, ALPHA / 4, v_min=1.0, c=1.0) / n
        lower = (mean_t - mean_c) - (hw_c + hw_t)
        detected += int(np.any(lower > 0, axis=1).sum())
        done += b
    return detected / N_RUNS


@pytest.mark.slow
def test_cs_detects_two_x_mde_effect_at_least_eighty_percent():
    """A true 2x-MDE win is detected in >= 80% of runs within N_MAX obs/arm.

    Verifies: the CS has usable power, not just valid coverage.
    Why it matters: a controller that can never conclude a win wastes traffic and
    never ships improvements — correct but worthless.
    Failure means: the boundary has regressed wider (or the effect is no longer
    detected), and the CS should be re-examined before relying on it.
    """
    power = _detection_rate(seed=7)
    assert power >= MIN_POWER, f"detection power {power} fell below {MIN_POWER}"
