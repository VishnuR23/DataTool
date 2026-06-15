"""Orchestrator behavior (ARCHITECTURE.md §9.3).

These pin the only component that touches the execution plane: that it clamps
allocations to the autonomous ceiling and records the clamp, honors min_step_dwell,
drives the correct state transitions, emits the right trust events and
notifications, sets the surface cooldown on revert, and records (not raises on)
adapter failures.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from datatool.control.decision_engine import ControlDecision
from datatool.control.orchestrator import execute, next_ramp_step
from datatool.control.runtime import ExperimentRuntime
from datatool.core.models import (
    Allocation,
    DecisionKind,
    ReversionPolicy,
    Scope,
    State,
)

from .conftest import make_contract

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


class FakeFlag:
    adapter_id = "flag.fake"

    def __init__(self, fail: bool = False):
        self.allocations: dict[str, float] = {"control": 100.0}
        self.killed = False
        self.fail = fail

    def assign(self, experiment_id: UUID, unit_id: str) -> str:
        return "control"

    def set_allocation(self, experiment_id: UUID, allocations: dict[str, float]) -> None:
        if self.fail:
            raise RuntimeError("flag backend unavailable")
        self.allocations = dict(allocations)

    def kill(self, experiment_id: UUID) -> None:
        if self.fail:
            raise RuntimeError("flag backend unavailable")
        self.killed = True
        self.allocations = {"control": 100.0}

    def get_allocation(self, experiment_id: UUID) -> dict[str, float]:
        return dict(self.allocations)

    def get_assignment_counts(self, experiment_id, since):
        return {}


class FakeNotifier:
    adapter_id = "notify.fake"

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    def send(self, event_kind: str, payload: dict) -> None:
        self.sent.append((event_kind, payload))


def _runtime(
    *, state=State.CANARY, alloc=1.0, last_ramp_at=None, has_promoted=False, approved=False
):
    return ExperimentRuntime(
        experiment_id=uuid4(),
        name="exp",
        surface="pricing-page",
        state=state,
        current_allocation_pct=alloc,
        started_at=NOW - timedelta(days=1),
        last_ramp_at=last_ramp_at,
        has_promoted=has_promoted,
        approval_notified=approved,
    )


def _decision(kind, target_state, *, target_alloc=None, counts=None, reason="r"):
    return ControlDecision(
        kind=kind,
        target_state=target_state,
        reason=reason,
        structured_reason={},
        target_allocation_pct=target_alloc,
        updated_breach_counts=counts or {},
    )


def _exec(decision, runtime, contract, flag=None, notifier=None):
    return execute(
        decision,
        runtime,
        contract,
        flag_provider=flag or FakeFlag(),
        notifier=notifier,
        now=NOW,
    )


# --------------------------------------------------------------------------- #
# next_ramp_step
# --------------------------------------------------------------------------- #


def test_next_ramp_step_picks_first_step_above_current():
    assert next_ramp_step(1.0, [1, 2.5, 5, 10, 25]) == 2.5
    assert next_ramp_step(0.0, [1, 2.5, 5]) == 1.0
    assert next_ramp_step(5.0, [1, 2.5, 5]) == 5.0  # at top: stays


# --------------------------------------------------------------------------- #
# CONTINUE
# --------------------------------------------------------------------------- #


def test_continue_makes_no_changes():
    rt = _runtime()
    flag = FakeFlag()
    result = _exec(_decision(DecisionKind.CONTINUE, None), rt, make_contract(), flag)
    assert result.actions == []
    assert result.transitions == []
    assert rt.state is State.CANARY
    assert rt.last_decision_at == NOW


# --------------------------------------------------------------------------- #
# RAMP + clamping + dwell
# --------------------------------------------------------------------------- #


def test_ramp_sets_allocation_and_transitions_to_ramping():
    rt = _runtime(state=State.CANARY, alloc=1.0)
    flag = FakeFlag()
    contract = make_contract(allocation=Allocation(ramp_schedule=[1, 2.5, 5], max_autonomous_pct=5))
    result = _exec(_decision(DecisionKind.RAMP, State.RAMPING), rt, contract, flag)
    assert flag.allocations == {"control": 97.5, "treatment": 2.5}
    assert rt.current_allocation_pct == 2.5
    assert rt.state is State.RAMPING
    assert rt.last_ramp_at == NOW
    assert result.actions[0].kind == "allocate"
    assert result.actions[0].clamped is False


def test_ramp_clamps_to_autonomous_ceiling_and_records_it():
    # Schedule jumps to 10 but the ceiling is 5: the ramp must be clamped to 5.
    rt = _runtime(alloc=2.5)
    flag = FakeFlag()
    contract = make_contract(
        allocation=Allocation(ramp_schedule=[1, 2.5, 10, 25], max_autonomous_pct=5)
    )
    result = _exec(_decision(DecisionKind.RAMP, State.RAMPING), rt, contract, flag)
    assert rt.current_allocation_pct == 5.0
    assert flag.allocations == {"control": 95.0, "treatment": 5.0}
    assert result.actions[0].clamped is True
    assert "ceiling" in result.actions[0].clamp_reason


def test_ramp_downgrades_to_hold_when_min_step_dwell_not_satisfied():
    rt = _runtime(state=State.RAMPING, alloc=2.5, last_ramp_at=NOW - timedelta(minutes=1))
    flag = FakeFlag()
    contract = make_contract(
        allocation=Allocation(
            ramp_schedule=[1, 2.5, 5], max_autonomous_pct=5, min_step_dwell=timedelta(hours=4)
        )
    )
    result = _exec(_decision(DecisionKind.RAMP, State.RAMPING), rt, contract, flag)
    assert rt.state is State.HOLDING
    assert result.actions == []  # no flag change
    assert flag.allocations == {"control": 100.0}  # untouched


def test_ramp_records_adapter_failure_without_transitioning():
    rt = _runtime(alloc=1.0)
    flag = FakeFlag(fail=True)
    contract = make_contract(allocation=Allocation(ramp_schedule=[1, 2.5, 5], max_autonomous_pct=5))
    result = _exec(_decision(DecisionKind.RAMP, State.RAMPING), rt, contract, flag)
    assert result.actions[0].succeeded is False
    assert result.actions[0].error
    assert rt.state is State.CANARY  # no transition on failure
    assert rt.current_allocation_pct == 1.0


# --------------------------------------------------------------------------- #
# PROMOTE
# --------------------------------------------------------------------------- #


def test_promote_sets_full_allocation_and_emits_clean_promotion():
    rt = _runtime(state=State.HOLDING, alloc=5.0)
    flag = FakeFlag()
    notifier = FakeNotifier()
    result = _exec(
        _decision(DecisionKind.PROMOTE, State.PROMOTING, target_alloc=100.0),
        rt,
        make_contract(),
        flag,
        notifier,
    )
    assert flag.allocations == {"control": 0.0, "treatment": 100.0}
    assert rt.state is State.PROMOTED
    assert rt.has_promoted is True
    # Passed through PROMOTING en route to PROMOTED.
    assert [t[1] for t in result.transitions] == [State.PROMOTING, State.PROMOTED]
    assert result.trust_event.kind == "clean_promotion"
    assert notifier.sent[0][0] == "promote"


# --------------------------------------------------------------------------- #
# REVERT
# --------------------------------------------------------------------------- #


def test_revert_kills_and_sets_cooldown_without_trust_event_when_never_promoted():
    rt = _runtime(state=State.CANARY, alloc=2.5, has_promoted=False)
    flag = FakeFlag()
    notifier = FakeNotifier()
    contract = make_contract(
        reversion=ReversionPolicy(cooldown=timedelta(hours=24), halt_related=True)
    )
    result = _exec(
        _decision(DecisionKind.REVERT, State.REVERTED, reason="goal.lost"),
        rt,
        contract,
        flag,
        notifier,
    )
    assert flag.killed is True
    assert rt.state is State.REVERTED
    assert rt.current_allocation_pct == 0.0
    assert result.trust_event is None  # plain revert, not a false-positive ship
    assert result.cooldown_until == NOW + timedelta(hours=24)
    assert result.halt_related is True
    assert notifier.sent[0][0] == "revert"


def test_revert_after_promotion_records_false_positive_ship():
    rt = _runtime(state=State.PROMOTED, alloc=100.0, has_promoted=True)
    flag = FakeFlag()
    contract = make_contract(scope=Scope(exclusivity_group="pricing"))
    result = _exec(
        _decision(DecisionKind.REVERT, State.REVERTED, reason="guardrail.error_rate.tripped"),
        rt,
        contract,
        flag,
    )
    assert rt.state is State.REVERTED
    assert result.trust_event.kind == "false_positive_ship"
    assert result.exclusivity_group == "pricing"


# --------------------------------------------------------------------------- #
# HOLD
# --------------------------------------------------------------------------- #


def test_hold_at_ceiling_sends_one_time_approval_notification():
    rt = _runtime(state=State.RAMPING, alloc=5.0, approved=False)
    notifier = FakeNotifier()
    contract = make_contract(
        allocation=Allocation(max_autonomous_pct=5.0, full_rollout_requires="human_approval")
    )
    _exec(
        _decision(DecisionKind.HOLD, State.HOLDING),
        rt,
        contract,
        flag=FakeFlag(),
        notifier=notifier,
    )
    assert rt.state is State.HOLDING
    assert rt.approval_notified is True
    assert notifier.sent[0][0] == "approval_requested"

    # A second hold does not re-notify.
    notifier2 = FakeNotifier()
    _exec(
        _decision(DecisionKind.HOLD, State.HOLDING),
        rt,
        contract,
        flag=FakeFlag(),
        notifier=notifier2,
    )
    assert notifier2.sent == []


def test_conclude_kills_traffic_and_transitions_to_concluded():
    rt = _runtime(state=State.RAMPING, alloc=5.0)
    flag = FakeFlag()
    result = _exec(
        _decision(DecisionKind.HOLD, State.CONCLUDED, reason="max_runtime"),
        rt,
        make_contract(),
        flag,
    )
    assert flag.killed is True
    assert rt.state is State.CONCLUDED
    assert result.actions[0].kind == "kill"


# --------------------------------------------------------------------------- #
# Bookkeeping
# --------------------------------------------------------------------------- #


def test_breach_counts_are_persisted_from_the_decision():
    rt = _runtime()
    decision = _decision(DecisionKind.CONTINUE, None, counts={"error_rate": 2})
    _exec(decision, rt, make_contract())
    assert rt.guardrail_breach_counts == {"error_rate": 2}


def test_promote_without_notifier_still_succeeds():
    rt = _runtime(state=State.HOLDING, alloc=5.0)
    flag = FakeFlag()
    result = _exec(
        _decision(DecisionKind.PROMOTE, State.PROMOTING, target_alloc=100.0),
        rt,
        make_contract(),
        flag,
        notifier=None,
    )
    assert rt.state is State.PROMOTED
    assert all(a.kind != "notify" for a in result.actions)
