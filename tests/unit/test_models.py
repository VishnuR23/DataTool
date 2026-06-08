"""Validation properties for the core domain models (ARCHITECTURE.md §6).

Each test names the invariant it pins. The contract is a safety artifact, so the
emphasis is on *rejection*: a malformed value must raise, never default silently.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from datatool.core.models import (
    Allocation,
    Decision,
    DecisionKind,
    ExperimentSpec,
    Goal,
    Graduation,
    GraduationRule,
    Guardrail,
    ReversionPolicy,
    Sample,
    Scope,
    Severity,
    Statistics,
    Threshold,
    ThresholdType,
    TrustContract,
    VariantSpec,
)

from .conftest import make_contract, make_guardrail

# --------------------------------------------------------------------------- #
# Threshold
# --------------------------------------------------------------------------- #


def test_absolute_threshold_accepts_any_finite_value():
    assert Threshold(type=ThresholdType.ABSOLUTE, value=2000).value == 2000
    assert Threshold(type=ThresholdType.ABSOLUTE, value=-3.5).value == -3.5


@pytest.mark.parametrize(
    "ttype", [ThresholdType.RELATIVE_INCREASE, ThresholdType.RELATIVE_DECREASE]
)
@pytest.mark.parametrize("bad", [0, -0.1, -100])
def test_relative_threshold_value_must_be_positive(ttype, bad):
    with pytest.raises(ValidationError):
        Threshold(type=ttype, value=bad)


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), float("-inf")])
def test_threshold_value_rejects_non_finite(bad):
    with pytest.raises(ValidationError):
        Threshold(type=ThresholdType.ABSOLUTE, value=bad)


# --------------------------------------------------------------------------- #
# Guardrail
# --------------------------------------------------------------------------- #


def test_guardrail_minimal_valid():
    g = make_guardrail()
    assert g.severity is Severity.HIGH
    assert g.consecutive_breaches_to_trip == 2


@pytest.mark.parametrize("field", ["name", "source", "metric"])
def test_guardrail_string_fields_must_be_non_empty(field):
    kwargs = dict(
        name="x",
        source="metrics.csv",
        metric="m",
        threshold=Threshold(type=ThresholdType.ABSOLUTE, value=1),
        window=timedelta(minutes=1),
    )
    kwargs[field] = ""
    with pytest.raises(ValidationError):
        Guardrail(**kwargs)


def test_guardrail_window_must_be_positive():
    with pytest.raises(ValidationError):
        Guardrail(
            name="x",
            source="s",
            metric="m",
            threshold=Threshold(type=ThresholdType.ABSOLUTE, value=1),
            window=timedelta(0),
        )


@pytest.mark.parametrize(
    "field,bad", [("min_samples_per_arm", 0), ("consecutive_breaches_to_trip", 0)]
)
def test_guardrail_counts_must_be_at_least_one(field, bad):
    kwargs = dict(
        name="x",
        source="s",
        metric="m",
        threshold=Threshold(type=ThresholdType.ABSOLUTE, value=1),
        window=timedelta(minutes=1),
    )
    kwargs[field] = bad
    with pytest.raises(ValidationError):
        Guardrail(**kwargs)


# --------------------------------------------------------------------------- #
# Goal
# --------------------------------------------------------------------------- #


def test_goal_direction_is_constrained():
    with pytest.raises(ValidationError):
        Goal(source="s", metric="m", direction="sideways")


@pytest.mark.parametrize("bad", [0, -0.01])
def test_goal_mde_must_be_positive(bad):
    with pytest.raises(ValidationError):
        Goal(source="s", metric="m", direction="increase", minimum_detectable_effect=bad)


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", [0, 1, -0.1, 1.5])
def test_alpha_must_be_strictly_between_zero_and_one(bad):
    with pytest.raises(ValidationError):
        Statistics(alpha=bad)


@pytest.mark.parametrize("bad", [0, -0.1, 1.5])
def test_fdr_budget_share_must_be_in_zero_one(bad):
    with pytest.raises(ValidationError):
        Statistics(fdr_budget_share=bad)


def test_fdr_budget_share_of_one_is_allowed():
    assert Statistics(fdr_budget_share=1.0).fdr_budget_share == 1.0


def test_max_runtime_must_exceed_min_runtime():
    with pytest.raises(ValidationError):
        Statistics(min_runtime=timedelta(days=2), max_runtime=timedelta(days=1))


def test_novelty_buffer_must_be_shorter_than_max_runtime():
    with pytest.raises(ValidationError):
        Statistics(
            min_runtime=timedelta(hours=1),
            max_runtime=timedelta(hours=2),
            novelty_buffer=timedelta(hours=2),
        )


def test_negative_durations_are_rejected():
    with pytest.raises(ValidationError):
        Statistics(novelty_buffer=timedelta(seconds=-1))


def test_enable_cuped_requires_a_positive_pre_period():
    with pytest.raises(ValidationError):
        Statistics(enable_cuped=True)
    with pytest.raises(ValidationError):
        Statistics(enable_cuped=True, cuped_pre_period=timedelta(0))
    ok = Statistics(enable_cuped=True, cuped_pre_period=timedelta(days=7))
    assert ok.cuped_pre_period == timedelta(days=7)


# --------------------------------------------------------------------------- #
# Scope
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", [-0.1, 100, 150])
def test_holdout_pct_must_be_in_zero_to_under_hundred(bad):
    with pytest.raises(ValidationError):
        Scope(holdout_pct=bad)


def test_scope_assignment_unit_is_constrained():
    with pytest.raises(ValidationError):
        Scope(assignment_unit="organization")


# --------------------------------------------------------------------------- #
# Allocation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", [0, -1, 101])
def test_initial_canary_pct_bounds(bad):
    with pytest.raises(ValidationError):
        Allocation(initial_canary_pct=bad, max_autonomous_pct=100)


@pytest.mark.parametrize("bad", [0, -1, 101])
def test_max_autonomous_pct_bounds(bad):
    with pytest.raises(ValidationError):
        Allocation(max_autonomous_pct=bad)


def test_ramp_schedule_must_be_non_empty():
    with pytest.raises(ValidationError):
        Allocation(ramp_schedule=[])


@pytest.mark.parametrize("bad", [[5, 5, 10], [10, 5], [1, 2, 2]])
def test_ramp_schedule_must_be_strictly_increasing(bad):
    with pytest.raises(ValidationError):
        Allocation(ramp_schedule=bad, max_autonomous_pct=100)


@pytest.mark.parametrize("bad", [[0, 5], [1, 150]])
def test_ramp_schedule_steps_must_be_in_zero_to_hundred(bad):
    with pytest.raises(ValidationError):
        Allocation(ramp_schedule=bad, max_autonomous_pct=100)


def test_initial_canary_cannot_exceed_autonomous_ceiling():
    with pytest.raises(ValidationError):
        Allocation(initial_canary_pct=10, max_autonomous_pct=5)


def test_min_step_dwell_cannot_be_negative():
    with pytest.raises(ValidationError):
        Allocation(min_step_dwell=timedelta(seconds=-1))


def test_ramp_schedule_may_exceed_autonomous_ceiling():
    # The ramp can describe steps beyond the autonomous ceiling; the clamp (not
    # the schedule) enforces the ceiling. See ARCHITECTURE.md §9.3.
    a = Allocation(ramp_schedule=[1, 2.5, 5, 10, 25], max_autonomous_pct=5)
    assert a.ramp_schedule[-1] == 25


# --------------------------------------------------------------------------- #
# ReversionPolicy / Graduation
# --------------------------------------------------------------------------- #


def test_reversion_cooldown_cannot_be_negative():
    with pytest.raises(ValidationError):
        ReversionPolicy(cooldown=timedelta(seconds=-1))


def test_graduation_review_cadence_must_be_positive():
    with pytest.raises(ValidationError):
        Graduation(review_cadence=timedelta(0))


@pytest.mark.parametrize("field", ["when", "action"])
def test_graduation_rule_clauses_must_be_non_empty(field):
    kwargs = {"when": {"x": 1}, "action": {"y": 2}}
    kwargs[field] = {}
    with pytest.raises(ValidationError):
        GraduationRule(**kwargs)


# --------------------------------------------------------------------------- #
# TrustContract
# --------------------------------------------------------------------------- #


def test_minimal_contract_is_valid():
    c = make_contract()
    assert c.version == "1.0"
    assert c.allocation.max_autonomous_pct == 5.0


def test_contract_rejects_duplicate_guardrail_names():
    with pytest.raises(ValidationError):
        make_contract(guardrails=[make_guardrail("dup"), make_guardrail("dup")])


def test_contract_allows_distinct_guardrail_names():
    c = make_contract(guardrails=[make_guardrail("a"), make_guardrail("b")])
    assert {g.name for g in c.guardrails} == {"a", "b"}


def test_contract_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        TrustContract.model_validate({**make_contract().model_dump(), "bogus": 1})


# --------------------------------------------------------------------------- #
# VariantSpec / ExperimentSpec
# --------------------------------------------------------------------------- #


def test_variant_source_is_constrained():
    with pytest.raises(ValidationError):
        VariantSpec(name="x", source="telepathy")


def _exp(variants):
    return dict(
        name="exp",
        surface="pricing-page",
        owner="growth",
        variants=variants,
        contract=make_contract(),
    )


def test_two_arm_experiment_with_one_control_is_valid(two_arm_variants):
    spec = ExperimentSpec(**_exp(two_arm_variants))
    assert len(spec.variants) == 2


def test_experiment_requires_exactly_two_variants(two_arm_variants):
    one = [VariantSpec(name="control", is_control=True, source="existing")]
    three = two_arm_variants + [VariantSpec(name="t2", source="static")]
    for variants in (one, three):
        with pytest.raises(ValidationError):
            ExperimentSpec(**_exp(variants))


def test_experiment_requires_exactly_one_control():
    no_control = [
        VariantSpec(name="a", source="static"),
        VariantSpec(name="b", source="static"),
    ]
    two_controls = [
        VariantSpec(name="a", is_control=True, source="existing"),
        VariantSpec(name="b", is_control=True, source="existing"),
    ]
    for variants in (no_control, two_controls):
        with pytest.raises(ValidationError):
            ExperimentSpec(**_exp(variants))


def test_experiment_rejects_duplicate_variant_names():
    dupes = [
        VariantSpec(name="same", is_control=True, source="existing"),
        VariantSpec(name="same", source="static"),
    ]
    with pytest.raises(ValidationError):
        ExperimentSpec(**_exp(dupes))


# --------------------------------------------------------------------------- #
# Sample / Decision
# --------------------------------------------------------------------------- #


def _sample(**overrides):
    kwargs = dict(
        experiment_id=uuid4(),
        variant_id=uuid4(),
        metric="signup",
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        window_end=datetime(2026, 1, 2, tzinfo=UTC),
        n=100,
        sum=42.0,
        sum_sq=42.0,
    )
    kwargs.update(overrides)
    return Sample(**kwargs)


def test_sample_minimal_valid():
    assert _sample().n == 100


def test_sample_n_cannot_be_negative():
    with pytest.raises(ValidationError):
        _sample(n=-1)


def test_sample_sum_sq_cannot_be_negative():
    with pytest.raises(ValidationError):
        _sample(sum_sq=-1.0)


def test_sample_window_end_must_be_after_start():
    with pytest.raises(ValidationError):
        _sample(
            window_start=datetime(2026, 1, 2, tzinfo=UTC),
            window_end=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_decision_reason_must_be_non_empty():
    with pytest.raises(ValidationError):
        Decision(experiment_id=uuid4(), kind=DecisionKind.CONTINUE, reason="")


def test_decision_minimal_valid():
    d = Decision(experiment_id=uuid4(), kind=DecisionKind.RAMP, reason="cs lower bound positive")
    assert d.kind is DecisionKind.RAMP
    assert d.structured_reason == {}
