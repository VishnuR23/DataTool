"""Online FDR calibration for the LORD++ controller (ARCHITECTURE.md §8.2).

The guarantee under test (Javanmard & Montanari 2018; LORD++ from Ramdas et al.
2017): across a sequential stream of independent tests, the expected false
discovery rate — the fraction of promotions that are mistakes — stays at or below
the target ``alpha``, no matter how many experiments run. Without this, an org
running many experiments ships a steady drip of false-positive winners purely by
chance, even though each experiment individually controls its type-I error.

Setup mirrors the brief: 200 sequential experiments per stream, 80% true nulls and
20% with a real effect; one-sided p-values from Z ~ N(mu, 1) with mu = 0 for nulls
and mu = 3.5 for alternatives (mu is the standardized 3x-MDE effect at the analysis
sample size, i.e. effect / standard-error). We reject experiment t when its p-value
is at or below ``next_alpha()`` and feed the outcome back. The realized FDR is
averaged over 1000 deterministic-seeded streams.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import norm

from datatool.stats.fdr import LORDController

TARGET_ALPHA = 0.05

# Slack above target. LORD *targets* FDR <= alpha and in practice realizes a value
# strictly below it, so the realized number sits under TARGET_ALPHA with room to
# spare; 0.02 generously covers Monte Carlo variation across 1000 streams while
# still failing loudly if the controller ever exceeded its target.
SLACK = 0.02

N_STREAMS = 1000
N_EXPERIMENTS = 200
NULL_FRACTION = 0.80
MU_ALTERNATIVE = 3.5


def _realized_fdr(seed: int) -> float:
    """Mean false discovery rate of LORD++ over many independent streams."""
    rng = np.random.default_rng(seed)
    fdrs = []
    for _ in range(N_STREAMS):
        is_null = rng.random(N_EXPERIMENTS) < NULL_FRACTION
        mu = np.where(is_null, 0.0, MU_ALTERNATIVE)
        z = rng.normal(mu, 1.0)
        p_values = 1.0 - norm.cdf(z)  # one-sided: large effect -> tiny p-value

        controller = LORDController(alpha=TARGET_ALPHA)
        false_rejections = 0
        total_rejections = 0
        for t in range(N_EXPERIMENTS):
            level = controller.next_alpha()
            rejected = bool(p_values[t] <= level)
            if rejected:
                total_rejections += 1
                if is_null[t]:
                    false_rejections += 1
            controller.record_outcome(rejected)

        # FDR contribution of this stream: false discovery *proportion* (0 if none).
        fdrs.append(false_rejections / total_rejections if total_rejections else 0.0)
    return float(np.mean(fdrs))


def test_lord_realized_fdr_at_or_below_target():
    """Realized FDR <= target + slack across 1000 streams.

    Verifies: LORD++ holds the org-wide false discovery rate at its target.
    Why it matters: this is what stops a busy experimentation program from
    accumulating false-positive ships as it scales the number of experiments.
    Failure means: the controller is over-spending its alpha wealth and the org
    ships losers at a rate above the promised FDR. Seed 2024 fixes the streams.
    """
    realized = _realized_fdr(seed=2024)
    assert realized <= TARGET_ALPHA + SLACK, (
        f"realized FDR {realized} exceeds {TARGET_ALPHA + SLACK}"
    )


# --------------------------------------------------------------------------- #
# Direct tests of the LORD++ update rule
# --------------------------------------------------------------------------- #


def test_level_decays_during_a_run_without_rejections():
    """With no discoveries, the level decays monotonically as gamma_t * w0.

    Verifies: between discoveries the controller becomes stricter, spending its
    wealth more cautiously the longer it goes without a win.
    """
    c = LORDController(alpha=0.05)
    levels = []
    for _ in range(10):
        levels.append(c.next_alpha())
        c.record_outcome(False)
    assert all(b < a for b, a in zip(levels[1:], levels, strict=False))


def test_a_rejection_raises_the_subsequent_level():
    """A discovery earns wealth, raising later levels above the no-discovery path.

    Verifies the core alpha-investing behavior: rejecting replenishes the budget,
    so subsequent tests are evaluated at a higher (more permissive) level than they
    would be without the discovery.
    """
    rejected = LORDController(alpha=0.05)
    never = LORDController(alpha=0.05)
    # First test: one rejects, the other does not.
    rejected.next_alpha()
    rejected.record_outcome(True)
    never.next_alpha()
    never.record_outcome(False)
    assert rejected.next_alpha() > never.next_alpha()


def test_level_is_always_positive():
    """next_alpha() is strictly positive at every step (wealth never fully drains).

    A zero level would make rejection impossible forever; the base gamma_t * w0 term
    keeps the budget alive.
    """
    c = LORDController(alpha=0.05)
    for _ in range(200):
        assert c.next_alpha() > 0
        c.record_outcome(False)


def test_gamma_weights_sum_to_one():
    """The discount sequence sums to 1, as the LORD construction requires.

    Verifies the normalization by zeta(exponent); a sequence not summing to 1 would
    invalidate the FDR guarantee.
    """
    c = LORDController(alpha=0.05)
    total = sum(c._gamma(k) for k in range(1, 200_000))
    assert abs(total - 1.0) < 1e-3  # tail beyond 200k is negligible


def test_record_outcome_tracks_tests_and_rejection_times():
    """The controller's state is exactly the tests-seen count and rejection times."""
    c = LORDController(alpha=0.05)
    for rejected in (False, True, False, True):
        c.next_alpha()
        c.record_outcome(rejected)
    assert c.tests_seen == 4
    assert c.rejections == [2, 4]


def test_invalid_parameters_raise():
    """Out-of-range alpha, wealth, or gamma exponent raise rather than mis-control.

    Verifies the no-silent-fallback rule for the controller's own configuration:
    w0 must lie in [0, alpha] and the gamma series must converge (exponent > 1).
    """
    import pytest

    from datatool.core.exceptions import StatisticsError

    for kwargs in (
        {"alpha": 0.0},
        {"alpha": 1.0},
        {"alpha": 0.05, "w0": 0.06},  # w0 > alpha
        {"alpha": 0.05, "w0": -0.01},  # w0 < 0
        {"alpha": 0.05, "gamma_exponent": 1.0},  # series does not converge
    ):
        with pytest.raises(StatisticsError):
            LORDController(**kwargs)
