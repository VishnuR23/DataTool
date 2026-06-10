"""Guardrail evaluation: thresholds, anti-flapping, and the min-samples gate.

Guardrails are the controller's fast safety reflex. These tests pin three things:
the threshold comparison is correct at the boundary for every threshold type; a
guardrail only trips on *consecutive* breaches (so transient noise cannot force a
revert); and below the sample floor the guardrail abstains instead of guessing.
The final property test asserts the evaluator can never raise or return an
inconsistent state for any valid input.
"""

from __future__ import annotations

from datetime import timedelta

from hypothesis import given
from hypothesis import strategies as st

from datatool.core.models import (
    DecisionKind,
    Guardrail,
    Severity,
    Threshold,
    ThresholdType,
)
from datatool.stats.guardrails import (
    GuardrailEvaluation,
    GuardrailStatus,
    evaluate_guardrail,
)


def _guardrail(
    ttype: ThresholdType,
    value: float,
    *,
    severity: Severity = Severity.HIGH,
    min_samples: int = 100,
    consecutive_to_trip: int = 2,
) -> Guardrail:
    return Guardrail(
        name="g",
        source="metrics.csv",
        metric="m",
        threshold=Threshold(type=ttype, value=value),
        window=timedelta(minutes=10),
        min_samples_per_arm=min_samples,
        severity=severity,
        consecutive_breaches_to_trip=consecutive_to_trip,
    )


def _eval(guardrail, control, treatment, prior=0, n=1000):
    return evaluate_guardrail(
        guardrail,
        control_value=control,
        treatment_value=treatment,
        n_control=n,
        n_treatment=n,
        prior_consecutive_breaches=prior,
    )


# --------------------------------------------------------------------------- #
# Anti-flapping
# --------------------------------------------------------------------------- #


def test_single_breach_does_not_trip_when_two_required():
    """One breach with consecutive_breaches_to_trip=2 records but does not trip.

    Verifies: the anti-flapping floor — a lone bad reading is not enough to revert.
    Why it matters: metrics are noisy; reverting on one blip wastes experiments and
    erodes trust in the controller.
    Failure means: the controller flaps, reverting on transient noise.
    """
    g = _guardrail(ThresholdType.ABSOLUTE, 2000, consecutive_to_trip=2)
    result = _eval(g, control=0, treatment=2500, prior=0)
    assert result.breached is True
    assert result.tripped is False
    assert result.status is GuardrailStatus.BREACHED
    assert result.consecutive_breaches == 1
    assert result.suggested_decision is None


def test_two_consecutive_breaches_trip():
    """Two breaches in a row reach the trip count and trip.

    Verifies: a sustained problem (the real failure signal) does trip.
    Failure means: genuine regressions are never acted on.
    """
    g = _guardrail(ThresholdType.ABSOLUTE, 2000, consecutive_to_trip=2)
    first = _eval(g, control=0, treatment=2500, prior=0)
    second = _eval(g, control=0, treatment=2500, prior=first.consecutive_breaches)
    assert second.tripped is True
    assert second.status is GuardrailStatus.TRIPPED
    assert second.consecutive_breaches == 2


def test_non_breach_between_breaches_resets_the_counter():
    """breach, then clean, then breach does NOT trip — the counter resets.

    Verifies: "consecutive" means consecutive; an intervening healthy cycle clears
    accumulated breaches.
    Why it matters: this is the precise definition that separates a flapping metric
    from a sustained regression.
    Failure means: non-adjacent breaches accumulate and trip spuriously.
    """
    g = _guardrail(ThresholdType.ABSOLUTE, 2000, consecutive_to_trip=2)
    r1 = _eval(g, control=0, treatment=2500, prior=0)
    assert r1.consecutive_breaches == 1
    r2 = _eval(g, control=0, treatment=100, prior=r1.consecutive_breaches)  # clean
    assert r2.breached is False
    assert r2.consecutive_breaches == 0
    assert r2.status is GuardrailStatus.OK
    r3 = _eval(g, control=0, treatment=2500, prior=r2.consecutive_breaches)
    assert r3.tripped is False
    assert r3.consecutive_breaches == 1


# --------------------------------------------------------------------------- #
# min_samples_per_arm gate
# --------------------------------------------------------------------------- #


def test_below_min_samples_returns_insufficient_data_not_breach():
    """Under the sample floor the guardrail abstains, not breaches.

    Verifies: small-sample noise cannot trip a guardrail; the status is explicitly
    "insufficient data".
    Failure means: early, data-starved cycles trigger false reverts.
    """
    g = _guardrail(ThresholdType.ABSOLUTE, 2000, min_samples=100)
    result = evaluate_guardrail(
        g, control_value=0, treatment_value=9999, n_control=50, n_treatment=50
    )
    assert result.status is GuardrailStatus.INSUFFICIENT_DATA
    assert result.breached is False
    assert result.value is None


def test_insufficient_data_preserves_prior_consecutive_count():
    """A data gap neither breaches nor resets; the prior counter is carried forward.

    Verifies: abstaining is a no-op for the anti-flapping state, so a transient gap
    does not erase an in-progress breach streak.
    """
    g = _guardrail(ThresholdType.ABSOLUTE, 2000, min_samples=100)
    result = evaluate_guardrail(
        g,
        control_value=0,
        treatment_value=9999,
        n_control=10,
        n_treatment=10,
        prior_consecutive_breaches=1,
    )
    assert result.status is GuardrailStatus.INSUFFICIENT_DATA
    assert result.consecutive_breaches == 1


# --------------------------------------------------------------------------- #
# Threshold semantics: boundary conditions for each type
# --------------------------------------------------------------------------- #


def test_absolute_threshold_boundaries():
    """absolute: breach iff treatment strictly exceeds the limit.

    Verifies: exactly-at-limit is within bounds; just above breaches; just below
    does not. The strict-exceedance rule is the documented convention.
    """
    g = _guardrail(ThresholdType.ABSOLUTE, 2000)
    assert _eval(g, control=0, treatment=2000).breached is False  # exactly at
    assert _eval(g, control=0, treatment=2000.01).breached is True  # just above
    assert _eval(g, control=0, treatment=1999.99).breached is False  # just below


def test_relative_increase_threshold_boundaries():
    """relative_increase: breach iff (treatment-control)/control exceeds threshold.

    Verifies: at control=100, threshold=0.20, treatment=120 is exactly +20% (not a
    breach), 120.01 breaches, 119 does not.
    """
    g = _guardrail(ThresholdType.RELATIVE_INCREASE, 0.20)
    assert _eval(g, control=100, treatment=120).breached is False  # exactly +20%
    assert _eval(g, control=100, treatment=120.01).breached is True  # just above
    assert _eval(g, control=100, treatment=119).breached is False  # +19%


def test_relative_decrease_threshold_boundaries():
    """relative_decrease: breach iff (control-treatment)/control exceeds threshold.

    Verifies: at control=100, threshold=0.05, treatment=95 is exactly -5% (not a
    breach), 94.9 breaches, 96 does not.
    """
    g = _guardrail(ThresholdType.RELATIVE_DECREASE, 0.05)
    assert _eval(g, control=100, treatment=95).breached is False  # exactly -5%
    assert _eval(g, control=100, treatment=94.9).breached is True  # just beyond
    assert _eval(g, control=100, treatment=96).breached is False  # -4%


def test_relative_increase_from_zero_baseline_is_unbounded_breach():
    """Any positive treatment against a zero control is an infinite increase.

    Verifies: the zero-baseline edge does not divide-by-zero; it breaches with an
    infinite realized value (and zero treatment is no change).
    """
    g = _guardrail(ThresholdType.RELATIVE_INCREASE, 0.20)
    breach = _eval(g, control=0, treatment=5)
    assert breach.breached is True
    assert breach.value == float("inf")
    assert _eval(g, control=0, treatment=0).breached is False


def test_relative_decrease_from_zero_baseline_never_breaches():
    """Nothing can decrease below a zero baseline, so it never breaches.

    Verifies: the zero-baseline edge for decrease is handled without error.
    """
    g = _guardrail(ThresholdType.RELATIVE_DECREASE, 0.05)
    assert _eval(g, control=0, treatment=0).breached is False
    assert _eval(g, control=0, treatment=10).breached is False


# --------------------------------------------------------------------------- #
# Severity -> decision mapping on trip
# --------------------------------------------------------------------------- #


def test_critical_and_high_trip_to_revert():
    """A tripped critical or high guardrail suggests REVERT (§8.4)."""
    for severity in (Severity.CRITICAL, Severity.HIGH):
        g = _guardrail(ThresholdType.ABSOLUTE, 2000, severity=severity, consecutive_to_trip=1)
        result = _eval(g, control=0, treatment=2500)
        assert result.tripped is True
        assert result.suggested_decision is DecisionKind.REVERT


def test_medium_trips_to_hold():
    """A tripped medium guardrail suggests HOLD, not REVERT (§8.4)."""
    g = _guardrail(ThresholdType.ABSOLUTE, 2000, severity=Severity.MEDIUM, consecutive_to_trip=1)
    result = _eval(g, control=0, treatment=2500)
    assert result.tripped is True
    assert result.suggested_decision is DecisionKind.HOLD


# --------------------------------------------------------------------------- #
# Property-based: never raises, never returns an inconsistent state
# --------------------------------------------------------------------------- #


@st.composite
def _guardrail_and_inputs(draw):
    ttype = draw(st.sampled_from(list(ThresholdType)))
    if ttype is ThresholdType.ABSOLUTE:
        value = draw(
            st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)
        )
    else:
        value = draw(
            st.floats(min_value=1e-6, max_value=10.0, allow_nan=False, allow_infinity=False)
        )
    g = _guardrail(
        ttype,
        value,
        severity=draw(st.sampled_from(list(Severity))),
        min_samples=draw(st.integers(min_value=1, max_value=1000)),
        consecutive_to_trip=draw(st.integers(min_value=1, max_value=5)),
    )
    finite = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)
    return {
        "guardrail": g,
        "control_value": draw(finite),
        "treatment_value": draw(finite),
        "n_control": draw(st.integers(min_value=0, max_value=5000)),
        "n_treatment": draw(st.integers(min_value=0, max_value=5000)),
        "prior_consecutive_breaches": draw(st.integers(min_value=0, max_value=10)),
    }


@given(_guardrail_and_inputs())
def test_evaluation_never_raises_and_state_is_consistent(case):
    """For any valid input the evaluator returns a self-consistent result.

    Verifies the safety invariants that the rest of the system relies on:
    tripping implies a breach and enough consecutive breaches; the suggested
    decision is present exactly when tripped; the value is never NaN; and the
    insufficient-data status agrees with the sample floor.
    Failure means: some input combination produces an undefined state the decision
    engine could act on incorrectly.
    """
    g = case["guardrail"]
    result = evaluate_guardrail(
        g,
        control_value=case["control_value"],
        treatment_value=case["treatment_value"],
        n_control=case["n_control"],
        n_treatment=case["n_treatment"],
        prior_consecutive_breaches=case["prior_consecutive_breaches"],
    )

    assert isinstance(result, GuardrailEvaluation)
    assert result.consecutive_breaches >= 0

    below_floor = (
        case["n_control"] < g.min_samples_per_arm or case["n_treatment"] < g.min_samples_per_arm
    )
    assert (result.status is GuardrailStatus.INSUFFICIENT_DATA) == below_floor

    if below_floor:
        assert result.breached is False
        assert result.tripped is False
        assert result.value is None
        assert result.consecutive_breaches == case["prior_consecutive_breaches"]
    else:
        assert result.value is not None
        assert not (result.value != result.value)  # not NaN
        if result.tripped:
            assert result.breached is True
            assert result.consecutive_breaches >= g.consecutive_breaches_to_trip
            assert result.suggested_decision in (DecisionKind.REVERT, DecisionKind.HOLD)
            expected = (
                DecisionKind.REVERT
                if g.severity in (Severity.CRITICAL, Severity.HIGH)
                else DecisionKind.HOLD
            )
            assert result.suggested_decision is expected
        else:
            assert result.suggested_decision is None
    # Tripping always implies a breach, regardless of branch.
    assert not (result.tripped and not result.breached)
