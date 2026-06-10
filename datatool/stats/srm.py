"""Sample ratio mismatch (SRM) detection.

A sample ratio mismatch is when the observed split of units across variants does
not match the configured allocation — a 50/50 experiment receiving a 52/48 split,
say. It is one of the most common and most corrosive A/B-testing failures: it
signals broken randomization, bot traffic, cache pollution, or instrumentation
loss, any of which silently bias every downstream comparison. An experiment with
SRM cannot be trusted to promote, no matter how good its goal metric looks.

We detect it with Pearson's chi-squared goodness-of-fit test of the observed
counts against the counts the configured allocation predicts. A very small
p-value means the observed split is wildly unlikely under correct randomization.
Per ARCHITECTURE.md §8.3 the controller treats ``p < 0.001`` as SRM and reverts;
this module only computes the p-value (a pure function), leaving the policy to the
decision engine.
"""

from __future__ import annotations

from scipy.stats import chisquare

from datatool.core.exceptions import InsufficientDataError, StatisticsError


def srm_p_value(observed: dict[str, int], expected_ratios: dict[str, float]) -> float:
    """Chi-squared p-value for a sample ratio mismatch.

    Parameters
    ----------
    observed:
        Mapping from variant name to the number of units assigned to it.
    expected_ratios:
        Mapping from variant name to its configured allocation share. Shares need
        not sum to 1 — they are normalised internally — but every share must be
        strictly positive.

    Returns
    -------
    The two-sided chi-squared goodness-of-fit p-value. Small (e.g. ``< 0.001``)
    means the observed split is implausible under the configured allocation: SRM.

    Raises
    ------
    StatisticsError
        If the two mappings describe different variant sets, if fewer than two
        variants are given (a chi-squared test needs at least two categories), if
        any count is negative, or if any expected share is non-positive.
    InsufficientDataError
        If no units have been observed at all (zero total) — there is nothing to
        test, and returning a p-value would be meaningless.
    """
    if set(observed) != set(expected_ratios):
        raise StatisticsError(
            f"observed variants {sorted(observed)} do not match configured variants "
            f"{sorted(expected_ratios)}"
        )
    # A chi-squared goodness-of-fit test is undefined for a single category: with
    # one variant there is no "ratio" to mismatch.
    if len(observed) < 2:
        raise StatisticsError(
            f"SRM needs at least two variants, got {len(observed)}: {sorted(observed)}"
        )

    variants = sorted(observed)  # deterministic ordering for reproducible arrays
    counts = [observed[v] for v in variants]
    ratios = [expected_ratios[v] for v in variants]

    if any(c < 0 for c in counts):
        raise StatisticsError(f"assignment counts must be non-negative, got {dict(observed)}")
    if any(r <= 0 for r in ratios):
        raise StatisticsError(
            f"expected allocation shares must be positive, got {dict(expected_ratios)}"
        )

    total = sum(counts)
    if total == 0:
        raise InsufficientDataError("no units observed in any variant; cannot evaluate SRM")

    # Expected counts under the configured allocation, normalised so they sum to
    # the same total as the observed counts (required by the chi-squared test).
    ratio_sum = sum(ratios)
    expected_counts = [total * (r / ratio_sum) for r in ratios]

    # Pearson chi-squared with k-1 degrees of freedom (scipy's default ddof=0).
    _, p_value = chisquare(f_obs=counts, f_exp=expected_counts)
    return float(p_value)
