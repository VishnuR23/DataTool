"""Sample ratio mismatch detection (ARCHITECTURE.md §8.3).

SRM is the tripwire for broken randomization. These tests verify the chi-squared
p-value is large when the observed split matches the configured allocation and
tiny when it does not, across balanced and skewed allocations, plus the edge
cases that would otherwise crash or silently mislead the controller.

The controller's decision threshold is p < 0.001 (a deliberately strict bar so
random noise does not trigger spurious reverts); tests assert against it.
"""

from __future__ import annotations

import pytest

from datatool.core.exceptions import InsufficientDataError, StatisticsError
from datatool.stats.srm import srm_p_value

SRM_THRESHOLD = 0.001


def test_srm_passes_on_exact_50_50_split():
    """A clean 5000/5000 split on a 50/50 allocation shows no SRM.

    Verifies: correct randomization produces a large p-value, so a healthy
    experiment is never falsely invalidated.
    Failure means: the test flags noise as mismatch and reverts good experiments.
    """
    p = srm_p_value({"control": 5000, "treatment": 5000}, {"control": 0.5, "treatment": 0.5})
    assert p > 0.05


def test_srm_detects_60_40_imbalance_on_50_50_allocation():
    """A 6000/4000 split on a 50/50 allocation is detected as SRM.

    Verifies: a gross imbalance (the canonical SRM symptom) yields p < 0.001.
    Why it matters: this is the exact footgun SRM detection exists to catch —
    biased assignment that would corrupt every downstream comparison.
    Failure means: the controller would promote on biased data.
    """
    p = srm_p_value({"control": 6000, "treatment": 4000}, {"control": 0.5, "treatment": 0.5})
    assert p < SRM_THRESHOLD


def test_srm_passes_on_matching_90_10_split():
    """A 4500/500 split on a 90/10 allocation shows no SRM.

    Verifies: detection respects intentionally skewed allocations — a 90/10 canary
    is correct, not a mismatch.
    Failure means: every skewed-allocation experiment is falsely flagged.
    """
    p = srm_p_value({"control": 4500, "treatment": 500}, {"control": 0.9, "treatment": 0.1})
    assert p > 0.05


def test_srm_detects_imbalance_on_90_10_allocation():
    """A 4000/1000 split on a 90/10 allocation is detected as SRM.

    Verifies: detection works on skewed allocations, not only balanced ones — the
    expected counts are weighted by the configured ratios.
    Failure means: SRM on canary experiments goes undetected.
    """
    p = srm_p_value({"control": 4000, "treatment": 1000}, {"control": 0.9, "treatment": 0.1})
    assert p < SRM_THRESHOLD


def test_srm_detects_empty_arm_as_mismatch():
    """An arm receiving zero of an expected 50% share is a severe SRM.

    Verifies: a totally starved arm (broken flag, instrumentation loss) is caught
    rather than treated as valid data.
    Failure means: an experiment exposing only one arm could still promote.
    """
    p = srm_p_value({"control": 5000, "treatment": 0}, {"control": 0.5, "treatment": 0.5})
    assert p < SRM_THRESHOLD


def test_srm_passes_on_balanced_three_way_split():
    """A 1000/1000/1000 split on an equal three-way allocation shows no SRM.

    Verifies: the test generalizes beyond two arms (k-1 degrees of freedom) and
    does not assume the MVP two-arm shape at the math layer.
    Failure means: multi-arm allocations are mis-evaluated.
    """
    p = srm_p_value(
        {"a": 1000, "b": 1000, "c": 1000},
        {"a": 1 / 3, "b": 1 / 3, "c": 1 / 3},
    )
    assert p > 0.05


def test_srm_detects_three_way_imbalance():
    """A skewed three-way split against an equal allocation is detected.

    Verifies: multi-arm SRM is caught, mirroring the two-arm behavior.
    Failure means: imbalance hides in multi-arm experiments.
    """
    p = srm_p_value(
        {"a": 1500, "b": 1000, "c": 500},
        {"a": 1 / 3, "b": 1 / 3, "c": 1 / 3},
    )
    assert p < SRM_THRESHOLD


def test_srm_raises_on_single_variant():
    """A single-variant input raises rather than returning a meaningless p-value.

    Verifies: the no-silent-fallback rule — a chi-squared goodness-of-fit test is
    undefined with one category, so the function refuses instead of guessing.
    """
    with pytest.raises(StatisticsError):
        srm_p_value({"control": 5000}, {"control": 1.0})


def test_srm_raises_on_zero_total_observations():
    """All-zero counts raise InsufficientDataError, not a divide-by-zero or NaN.

    Verifies: with no data there is nothing to test; the function fails loudly.
    """
    with pytest.raises(InsufficientDataError):
        srm_p_value({"control": 0, "treatment": 0}, {"control": 0.5, "treatment": 0.5})


def test_srm_raises_on_mismatched_variant_sets():
    """Differing keys between observed and expected raise (a wiring bug).

    Verifies: the function does not silently ignore or invent a variant.
    """
    with pytest.raises(StatisticsError):
        srm_p_value({"control": 100, "treatment": 100}, {"control": 0.5, "variant_b": 0.5})


def test_srm_raises_on_negative_count():
    """A negative assignment count is structurally invalid and raises."""
    with pytest.raises(StatisticsError):
        srm_p_value({"control": -1, "treatment": 100}, {"control": 0.5, "treatment": 0.5})


def test_srm_raises_on_non_positive_expected_share():
    """A zero/negative configured share is invalid (would make expected count 0)."""
    with pytest.raises(StatisticsError):
        srm_p_value({"control": 100, "treatment": 100}, {"control": 1.0, "treatment": 0.0})
