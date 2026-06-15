"""End-to-end control-loop integration (ARCHITECTURE.md §9, §17 integration tier).

Unit tests pin each component in isolation; these drive the *real* decision engine,
orchestrator, and ledger together through whole experiment lifecycles against
in-memory fakes — the proof that the pieces compose into the behavior §9 describes:
ramp to the ceiling and hold for approval, promote, graduate; revert on a guardrail
breach; revert on a decisive loss.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from datatool.control.decision_engine import ControlDecision, decide
from datatool.control.ledger import SurfaceTrustState, evaluate_graduation
from datatool.control.orchestrator import execute
from datatool.control.runtime import (
    AssignmentObservation,
    ExperimentRuntime,
    GoalObservation,
    GuardrailObservation,
)
from datatool.core.models import (
    Allocation,
    Authorization,
    DecisionKind,
    Goal,
    Graduation,
    GraduationRule,
    Guardrail,
    ReversionPolicy,
    Scope,
    Severity,
    State,
    Statistics,
    Threshold,
    ThresholdType,
    TrustContract,
)
from datatool.stats.confidence_sequence import ArmStats

GOAL_ALPHA = 0.05


class FakeFlag:
    adapter_id = "flag.fake"

    def __init__(self):
        self.allocations = {"control": 100.0}
        self.killed = False

    def assign(self, experiment_id: UUID, unit_id: str) -> str:
        return "control"

    def set_allocation(self, experiment_id: UUID, allocations: dict[str, float]) -> None:
        self.allocations = dict(allocations)

    def kill(self, experiment_id: UUID) -> None:
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


def _contract(**over) -> TrustContract:
    base = dict(
        scope=Scope(),
        allocation=Allocation(
            ramp_schedule=[1, 2.5, 5], max_autonomous_pct=5.0, min_step_dwell=timedelta(hours=4)
        ),
        guardrails=[],
        goal=Goal(source="metrics.csv", metric="signup_rate", direction="increase"),
        statistics=Statistics(),
        reversion=ReversionPolicy(),
        graduation=Graduation(),
        authorization=Authorization(),
    )
    base.update(over)
    return TrustContract(**base)


def _arm(mean: float, n: int = 5000) -> ArmStats:
    s = mean * n
    return ArmStats(n=n, sum=s, sum_sq=s, max_value=1.0)


def _runtime(started: datetime) -> ExperimentRuntime:
    return ExperimentRuntime(
        experiment_id=uuid4(),
        name="exp",
        surface="pricing-page",
        state=State.CANARY,
        current_allocation_pct=1.0,
        started_at=started,
    )


_HEALTHY_SPLIT = AssignmentObservation(
    counts={"control": 5000, "treatment": 5000},
    expected_ratios={"control": 0.5, "treatment": 0.5},
)


def test_winning_experiment_ramps_to_ceiling_then_holds_for_approval():
    """A decisive winner climbs the ramp schedule to the ceiling and holds for a human."""
    contract = _contract()
    start = datetime(2026, 6, 1, tzinfo=UTC)
    now = start + timedelta(days=2)  # past novelty + min_runtime
    rt = _runtime(start)
    flag, notifier = FakeFlag(), FakeNotifier()
    win = GoalObservation(control=_arm(0.5), treatment=_arm(0.7))

    seen_allocations = [rt.current_allocation_pct]
    for _ in range(8):
        decision = decide(
            rt,
            contract,
            now=now,
            goal=win,
            guardrails={},
            assignments=_HEALTHY_SPLIT,
            goal_alpha=GOAL_ALPHA,
        )
        execute(decision, rt, contract, flag_provider=flag, notifier=notifier, now=now)
        seen_allocations.append(rt.current_allocation_pct)
        now += timedelta(hours=5)  # clear the 4h min_step_dwell each cycle
        if rt.state is State.HOLDING:
            break

    assert rt.state is State.HOLDING
    assert rt.current_allocation_pct == 5.0  # the autonomous ceiling
    assert 2.5 in seen_allocations and 5.0 in seen_allocations  # climbed the schedule
    assert any(kind == "approval_requested" for kind, _ in notifier.sent)


def test_full_lifecycle_promote_then_graduate():
    """After holding for approval, a human promote ships; a clean record then graduates."""
    contract = _contract()
    start = datetime(2026, 6, 1, tzinfo=UTC)
    now = start + timedelta(days=2)
    rt = _runtime(start)
    flag, notifier = FakeFlag(), FakeNotifier()
    win = GoalObservation(control=_arm(0.5), treatment=_arm(0.7))

    for _ in range(8):
        decision = decide(
            rt,
            contract,
            now=now,
            goal=win,
            guardrails={},
            assignments=_HEALTHY_SPLIT,
            goal_alpha=GOAL_ALPHA,
        )
        execute(decision, rt, contract, flag_provider=flag, notifier=notifier, now=now)
        now += timedelta(hours=5)
        if rt.state is State.HOLDING:
            break

    # Human approves -> promote (simulated by executing a PROMOTE decision).
    promote = ControlDecision(
        kind=DecisionKind.PROMOTE,
        target_state=State.PROMOTING,
        reason="human approved full rollout",
        structured_reason={},
        target_allocation_pct=100.0,
    )
    result = execute(promote, rt, contract, flag_provider=flag, notifier=notifier, now=now)
    assert rt.state is State.PROMOTED
    assert flag.allocations == {"control": 0.0, "treatment": 100.0}
    assert result.trust_event.kind == "clean_promotion"

    # Ledger: this promotion plus two earlier clean ones (3 total) graduates autonomy.
    graduation = Graduation(
        rules=[
            GraduationRule(
                when={"clean_promotions_on_surface": ">=3", "false_positive_ships": 0},
                action={"field": "max_autonomous_pct", "op": "increase", "by": 5, "up_to": 25},
            )
        ]
    )
    surface = SurfaceTrustState(
        surface="pricing-page",
        clean_promotions_on_surface=3,
        false_positive_ships=0,
        days_since_last_revert=None,
        current_field_values={"max_autonomous_pct": 5.0},
    )
    outcomes = evaluate_graduation(graduation, surface, now=now)
    assert len(outcomes) == 1
    assert outcomes[0].kind == "graduate"
    assert outcomes[0].new_value == 10.0


def test_guardrail_breach_reverts_after_consecutive_trips():
    """A critical guardrail that breaches twice in a row reverts the experiment."""
    guardrail = Guardrail(
        name="error_rate",
        source="metrics.csv",
        metric="error_rate",
        threshold=Threshold(type=ThresholdType.RELATIVE_INCREASE, value=0.2),
        window=timedelta(minutes=10),
        min_samples_per_arm=100,
        severity=Severity.CRITICAL,
        consecutive_breaches_to_trip=2,
    )
    contract = _contract(guardrails=[guardrail])
    start = datetime(2026, 6, 1, tzinfo=UTC)
    now = start + timedelta(days=2)
    rt = _runtime(start)
    flag = FakeFlag()
    inconclusive = GoalObservation(control=_arm(0.50), treatment=_arm(0.51))
    breaching = {
        "error_rate": GuardrailObservation(
            control_value=100, treatment_value=200, n_control=1000, n_treatment=1000
        )
    }

    # Cycle 1: first breach -> counter 1, not yet tripped -> continue.
    d1 = decide(
        rt,
        contract,
        now=now,
        goal=inconclusive,
        guardrails=breaching,
        assignments=_HEALTHY_SPLIT,
        goal_alpha=GOAL_ALPHA,
    )
    execute(d1, rt, contract, flag_provider=flag, now=now)
    assert d1.kind is DecisionKind.CONTINUE
    assert rt.guardrail_breach_counts["error_rate"] == 1

    # Cycle 2: second consecutive breach -> trip -> revert.
    now += timedelta(minutes=1)
    d2 = decide(
        rt,
        contract,
        now=now,
        goal=inconclusive,
        guardrails=breaching,
        assignments=_HEALTHY_SPLIT,
        goal_alpha=GOAL_ALPHA,
    )
    execute(d2, rt, contract, flag_provider=flag, now=now)
    assert d2.kind is DecisionKind.REVERT
    assert rt.state is State.REVERTED
    assert flag.killed is True


def test_decisive_loss_reverts():
    """An experiment whose treatment is decisively worse reverts with goal.lost."""
    contract = _contract()
    start = datetime(2026, 6, 1, tzinfo=UTC)
    now = start + timedelta(days=2)
    rt = _runtime(start)
    flag = FakeFlag()
    loss = GoalObservation(control=_arm(0.5), treatment=_arm(0.3))

    decision = decide(
        rt,
        contract,
        now=now,
        goal=loss,
        guardrails={},
        assignments=_HEALTHY_SPLIT,
        goal_alpha=GOAL_ALPHA,
    )
    execute(decision, rt, contract, flag_provider=flag, now=now)
    assert decision.reason == "goal.lost"
    assert rt.state is State.REVERTED
    assert flag.killed is True
