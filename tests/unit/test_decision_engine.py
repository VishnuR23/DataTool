"""Decision engine branch coverage (ARCHITECTURE.md §9.2).

Each test pins one branch of the decision logic and the safety ordering between
branches (pre-checks before guardrails before goal). The goal-metric arms are
constructed so the confidence sequence is unambiguously decisive (or not), so the
tests assert the controller's *judgement*, not a knife-edge statistical outcome.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from datatool.control.decision_engine import decide
from datatool.control.runtime import (
    AssignmentObservation,
    ExperimentRuntime,
    GoalObservation,
    GuardrailObservation,
)
from datatool.core.models import (
    Allocation,
    DecisionKind,
    Goal,
    Severity,
    State,
    Statistics,
)
from datatool.stats.confidence_sequence import ArmStats

from .conftest import make_contract, make_guardrail

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
GOAL_ALPHA = 0.05


def _runtime(*, state=State.CANARY, alloc=1.0, started_delta=timedelta(days=2), counts=None):
    return ExperimentRuntime(
        experiment_id=__import__("uuid").uuid4(),
        name="exp",
        surface="pricing-page",
        state=state,
        current_allocation_pct=alloc,
        started_at=NOW - started_delta,
        guardrail_breach_counts=counts or {},
    )


# Bernoulli sufficient stats (x in {0,1} => sum_sq == sum). n=5000 per arm.
def _arm(mean: float, n: int = 5000) -> ArmStats:
    s = mean * n
    return ArmStats(n=n, sum=s, sum_sq=s, max_value=1.0)


def _win_goal():  # treatment clearly above control
    return GoalObservation(control=_arm(0.5), treatment=_arm(0.7))


def _loss_goal():  # treatment clearly below control
    return GoalObservation(control=_arm(0.5), treatment=_arm(0.3))


def _inconclusive_goal():  # nearly equal arms; CS spans 0
    return GoalObservation(control=_arm(0.50), treatment=_arm(0.51))


def _decide(runtime, contract, *, goal=None, guardrails=None, assignments=None):
    return decide(
        runtime,
        contract,
        now=NOW,
        goal=goal,
        guardrails=guardrails or {},
        assignments=assignments,
        goal_alpha=GOAL_ALPHA,
    )


# --------------------------------------------------------------------------- #
# Hard pre-checks
# --------------------------------------------------------------------------- #


def test_past_max_runtime_concludes_without_shipping():
    """Past max_runtime the experiment concludes (terminal, no ship), beating every
    other signal — even a decisive winning goal does not ship after the deadline."""
    contract = make_contract()
    d = _decide(_runtime(started_delta=timedelta(days=20)), contract, goal=_win_goal())
    assert d.target_state is State.CONCLUDED
    assert "max_runtime" in d.reason


def test_within_novelty_buffer_continues():
    """Inside the novelty buffer all decisions are suppressed (CONTINUE)."""
    contract = make_contract()
    d = _decide(_runtime(started_delta=timedelta(hours=1)), contract, goal=_win_goal())
    assert d.kind is DecisionKind.CONTINUE
    assert d.target_state is None


def test_srm_failure_reverts_before_goal_is_considered():
    """A sample-ratio mismatch reverts with reason srm_failed, before goal stats."""
    contract = make_contract()
    assignments = AssignmentObservation(
        counts={"control": 6000, "treatment": 4000},
        expected_ratios={"control": 0.5, "treatment": 0.5},
    )
    d = _decide(_runtime(), contract, goal=_win_goal(), assignments=assignments)
    assert d.kind is DecisionKind.REVERT
    assert d.reason == "srm_failed"
    assert d.target_state is State.REVERTED


def test_healthy_assignment_split_does_not_trigger_srm():
    """A clean 50/50 split passes SRM and the engine proceeds to the goal."""
    contract = make_contract()
    assignments = AssignmentObservation(
        counts={"control": 5000, "treatment": 5000},
        expected_ratios={"control": 0.5, "treatment": 0.5},
    )
    d = _decide(_runtime(), contract, goal=_win_goal(), assignments=assignments)
    assert d.kind is DecisionKind.RAMP  # reached goal evaluation


def test_srm_skipped_below_minimum_assignments():
    """Below the SRM sample floor, SRM is not evaluated and the goal drives the call."""
    contract = make_contract()
    assignments = AssignmentObservation(
        counts={"control": 6, "treatment": 4},  # imbalanced but tiny
        expected_ratios={"control": 0.5, "treatment": 0.5},
    )
    d = _decide(_runtime(), contract, goal=_win_goal(), assignments=assignments)
    assert d.kind is DecisionKind.RAMP


# --------------------------------------------------------------------------- #
# Guardrails
# --------------------------------------------------------------------------- #


def test_critical_guardrail_trip_reverts_before_goal():
    """A tripped critical guardrail reverts immediately, ignoring a winning goal."""
    g = make_guardrail("error_rate")
    g = g.model_copy(update={"severity": Severity.CRITICAL, "consecutive_breaches_to_trip": 1})
    contract = make_contract(guardrails=[g])
    obs = {
        "error_rate": GuardrailObservation(
            control_value=100, treatment_value=200, n_control=1000, n_treatment=1000
        )
    }
    d = _decide(_runtime(), contract, goal=_win_goal(), guardrails=obs)
    assert d.kind is DecisionKind.REVERT
    assert d.reason == "guardrail.error_rate.tripped"


def test_medium_guardrail_trip_holds():
    """A tripped medium guardrail holds (does not revert), overriding a ramp."""
    g = make_guardrail("error_rate").model_copy(
        update={"severity": Severity.MEDIUM, "consecutive_breaches_to_trip": 1}
    )
    contract = make_contract(guardrails=[g])
    obs = {
        "error_rate": GuardrailObservation(
            control_value=100, treatment_value=200, n_control=1000, n_treatment=1000
        )
    }
    d = _decide(_runtime(), contract, goal=_win_goal(), guardrails=obs)
    assert d.kind is DecisionKind.HOLD
    assert d.target_state is State.HOLDING


def test_guardrail_breach_counts_are_updated_in_the_decision():
    """The decision carries forward the updated anti-flapping counters for the caller."""
    g = make_guardrail("error_rate").model_copy(update={"consecutive_breaches_to_trip": 3})
    contract = make_contract(guardrails=[g])
    obs = {
        "error_rate": GuardrailObservation(
            control_value=100, treatment_value=200, n_control=1000, n_treatment=1000
        )
    }
    d = _decide(
        _runtime(counts={"error_rate": 1}), contract, goal=_inconclusive_goal(), guardrails=obs
    )
    # Breached again -> counter goes 1 -> 2, not yet tripped (needs 3).
    assert d.updated_breach_counts["error_rate"] == 2
    assert d.kind is DecisionKind.CONTINUE  # goal inconclusive, guardrail not tripped


# --------------------------------------------------------------------------- #
# Goal evaluation
# --------------------------------------------------------------------------- #


def test_decisive_win_below_ceiling_ramps():
    """A decisive winning goal below the autonomous ceiling ramps."""
    contract = make_contract(allocation=Allocation(max_autonomous_pct=5.0))
    d = _decide(_runtime(alloc=1.0), contract, goal=_win_goal())
    assert d.kind is DecisionKind.RAMP
    assert d.target_state is State.RAMPING


def test_decisive_win_at_ceiling_with_human_approval_holds():
    """At the ceiling under human-approval rollout, a decisive win holds for approval."""
    contract = make_contract(
        allocation=Allocation(max_autonomous_pct=5.0, full_rollout_requires="human_approval")
    )
    d = _decide(_runtime(alloc=5.0), contract, goal=_win_goal())
    assert d.kind is DecisionKind.HOLD
    assert "approval" in d.reason


def test_decisive_win_at_ceiling_autonomous_promotes():
    """At the ceiling under autonomous rollout (past min_runtime), a decisive win promotes."""
    contract = make_contract(
        allocation=Allocation(max_autonomous_pct=5.0, full_rollout_requires="autonomous")
    )
    d = _decide(_runtime(alloc=5.0, started_delta=timedelta(days=2)), contract, goal=_win_goal())
    assert d.kind is DecisionKind.PROMOTE
    assert d.target_state is State.PROMOTING
    assert d.target_allocation_pct == 100.0


def test_decisive_win_at_ceiling_autonomous_holds_before_min_runtime():
    """Autonomous rollout still holds at the ceiling until min_runtime elapses."""
    contract = make_contract(
        allocation=Allocation(max_autonomous_pct=5.0, full_rollout_requires="autonomous"),
        statistics=Statistics(novelty_buffer=timedelta(hours=6), min_runtime=timedelta(days=1)),
    )
    # 12h: past the 6h novelty buffer but before the 24h min_runtime.
    d = _decide(_runtime(alloc=5.0, started_delta=timedelta(hours=12)), contract, goal=_win_goal())
    assert d.kind is DecisionKind.HOLD
    assert "min_runtime" in d.reason


def test_decisive_loss_reverts_with_goal_lost():
    """A decisive losing goal reverts with reason goal.lost."""
    contract = make_contract()
    d = _decide(_runtime(), contract, goal=_loss_goal())
    assert d.kind is DecisionKind.REVERT
    assert d.reason == "goal.lost"


def test_inconclusive_goal_continues():
    """A goal whose confidence sequence still spans zero continues."""
    contract = make_contract()
    d = _decide(_runtime(), contract, goal=_inconclusive_goal())
    assert d.kind is DecisionKind.CONTINUE
    assert d.target_state is None


def test_insufficient_goal_data_continues():
    """With an empty goal arm the engine continues rather than erroring."""
    contract = make_contract()
    goal = GoalObservation(control=_arm(0.5, n=0), treatment=_arm(0.5, n=10))
    d = _decide(_runtime(), contract, goal=goal)
    assert d.kind is DecisionKind.CONTINUE


def test_decrease_goal_direction_treats_lower_treatment_as_a_win():
    """For a decrease goal, a treatment mean below control is the win and ramps."""
    contract = make_contract(
        goal=Goal(source="metrics.csv", metric="latency", direction="decrease")
    )
    # treatment below control => improvement for a 'decrease' goal.
    d = _decide(_runtime(alloc=1.0), contract, goal=_loss_goal())
    assert d.kind is DecisionKind.RAMP


# --------------------------------------------------------------------------- #
# Projection to the persisted Decision model
# --------------------------------------------------------------------------- #


def test_control_decision_projects_to_valid_decision_model():
    """to_decision() yields a valid Decision with a suggested action for a ramp."""
    contract = make_contract()
    cd = _decide(_runtime(alloc=1.0), contract, goal=_win_goal())
    decision = cd.to_decision(experiment_id=__import__("uuid").uuid4())
    assert decision.kind is DecisionKind.RAMP
    assert decision.suggested_action == {"action": "ramp_to_next_step"}
    assert "cs_lower" in decision.structured_reason
