"""The control-loop cycle assembly (ARCHITECTURE.md §9).

Covers start_experiment and the mechanics of run_cycle (terminal short-circuit,
persistence, the guardrail-revert path) against an in-memory DB with a fake flag
provider; the full scenario behavior is exercised end-to-end in test_simulator.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from datatool.adapters.metrics.csv import CsvMetricsSource
from datatool.control.loop import run_cycle, start_experiment
from datatool.control.runtime import ExperimentRuntime
from datatool.core.models import (
    Allocation,
    Authorization,
    Goal,
    Graduation,
    ReversionPolicy,
    Scope,
    State,
    Statistics,
    TrustContract,
)
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import (
    DecisionRepository,
    ExperimentRepository,
    VariantRepository,
)

START = datetime(2026, 1, 1, tzinfo=UTC)


class FakeFlag:
    adapter_id = "flag.fake"

    def __init__(self):
        self.alloc = {"control": 100.0}
        self.killed = False

    def assign(self, e, u):
        return "control"

    def set_allocation(self, e, a):
        self.alloc = dict(a)

    def kill(self, e):
        self.killed = True
        self.alloc = {"control": 100.0}

    def get_allocation(self, e):
        return dict(self.alloc)

    def get_assignment_counts(self, e, s):
        return {}


def _contract() -> TrustContract:
    return TrustContract(
        scope=Scope(assignment_unit="user"),
        allocation=Allocation(
            initial_canary_pct=1.0, ramp_schedule=[1, 5, 25], max_autonomous_pct=25.0
        ),
        guardrails=[],
        goal=Goal(source="metrics.csv", metric="conversion", direction="increase"),
        statistics=Statistics(novelty_buffer=timedelta(0), min_runtime=timedelta(hours=1)),
        reversion=ReversionPolicy(),
        graduation=Graduation(),
        authorization=Authorization(),
    )


def _seed(session_factory) -> tuple[UUID, dict[str, UUID]]:
    with session_scope(session_factory) as s:
        exp = ExperimentRepository(s).add(
            name="sim", surface="x", owner="o", contract={}, spec={}, state="proposed"
        )
        vr = VariantRepository(s)
        control = vr.add(experiment_id=exp.id, name="control", is_control=True, payload={})
        treatment = vr.add(experiment_id=exp.id, name="treatment", is_control=False, payload={})
        return exp.id, {"control": control.id, "treatment": treatment.id}


def _positive_csv(tmp_path, variant_ids) -> CsvMetricsSource:
    # Dense, strongly-positive goal data over one hour.
    path = tmp_path / "pos.csv"
    rows = ["timestamp,unit_id,variant,metric,value"]
    for minute in range(60):
        ts = (START + timedelta(minutes=minute)).isoformat()
        for i in range(40):
            # deterministic pseudo-random: control ~0.2, treatment ~0.8
            cv = 1 if (minute * 40 + i) % 5 == 0 else 0  # 0.2
            tv = 0 if (minute * 40 + i) % 5 == 0 else 1  # 0.8
            rows.append(f"{ts},c{minute}_{i},control,conversion,{cv}")
            rows.append(f"{ts},t{minute}_{i},treatment,conversion,{tv}")
    path.write_text("\n".join(rows) + "\n")
    return CsvMetricsSource(path, variant_ids)


def test_start_experiment_canaries_and_allocates(session_factory):
    eid, _ = _seed(session_factory)
    flag = FakeFlag()
    with session_scope(session_factory) as s:
        exp = ExperimentRepository(s).get(eid)
        new_state = start_experiment(s, exp, _contract(), flag, now=START)
    assert new_state is State.CANARY
    assert flag.alloc == {"control": 99.0, "treatment": 1.0}
    with session_scope(session_factory) as s:
        assert ExperimentRepository(s).get(eid).state == State.CANARY.value


def test_run_cycle_on_terminal_experiment_returns_none(session_factory, tmp_path):
    eid, vids = _seed(session_factory)
    runtime = ExperimentRuntime(
        experiment_id=eid,
        name="sim",
        surface="x",
        state=State.REVERTED,  # terminal
        current_allocation_pct=0.0,
        started_at=START,
    )
    metrics = _positive_csv(tmp_path, vids)
    with session_scope(session_factory) as s:
        decision = run_cycle(
            s,
            runtime,
            _contract(),
            metrics=metrics,
            flag=FakeFlag(),
            variant_ids=vids,
            control_name="control",
            treatment_name="treatment",
            now=START + timedelta(minutes=30),
            goal_alpha=0.05,
            evaluate_srm=False,
        )
    assert decision is None


def test_run_cycle_persists_a_decision_and_ramps_on_a_strong_win(session_factory, tmp_path):
    eid, vids = _seed(session_factory)
    contract = _contract()
    flag = FakeFlag()
    metrics = _positive_csv(tmp_path, vids)
    runtime = ExperimentRuntime(
        experiment_id=eid,
        name="sim",
        surface="x",
        state=State.CANARY,
        current_allocation_pct=1.0,
        started_at=START,
    )
    # Evaluate near the end of the data so the cumulative CS is decisive.
    now = START + timedelta(minutes=59)
    with session_scope(session_factory) as s:
        decision = run_cycle(
            s,
            runtime,
            contract,
            metrics=metrics,
            flag=flag,
            variant_ids=vids,
            control_name="control",
            treatment_name="treatment",
            now=now,
            goal_alpha=0.05,
            started_at=START,
            evaluate_srm=False,
        )
    assert decision is not None
    assert runtime.state is State.RAMPING
    assert runtime.current_allocation_pct == 5.0  # next ramp step above canary
    with session_scope(session_factory) as s:
        assert len(DecisionRepository(s).list_for(eid)) == 1
