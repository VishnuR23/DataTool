"""The control-plane daemon tick (ARCHITECTURE.md §9.1).

Exercises run_one_tick's mechanics against a file-backed SQLite DB with the real
Postgres flag adapter and an injected fake metrics resolver: proposed experiments are
started, due running experiments are evaluated (and conclude through run_cycle),
not-due and terminal experiments are skipped, and LORD advances on conclusion.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from datatool.adapters.flag.postgres import PostgresFlagProvider
from datatool.control.daemon import run, run_one_tick
from datatool.core.models import (
    Allocation,
    Authorization,
    Goal,
    Graduation,
    Guardrail,
    ReversionPolicy,
    Sample,
    Scope,
    Severity,
    State,
    Statistics,
    Threshold,
    ThresholdType,
    TrustContract,
)
from datatool.persistence.db import init_db, make_engine, make_session_factory, session_scope
from datatool.persistence.repositories import (
    DecisionRepository,
    ExperimentRepository,
    FlagAllocationRepository,
    VariantRepository,
)
from datatool.stats.fdr import LORDController

WINDOW_START = datetime(2026, 6, 1, tzinfo=UTC)
WINDOW_END = datetime(2026, 6, 1, 1, tzinfo=UTC)


@pytest.fixture
def factory(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'daemon.db'}"
    init_db(make_engine(url))
    return make_session_factory(make_engine(url))


def _contract(*, consecutive: int = 2) -> TrustContract:
    return TrustContract(
        scope=Scope(assignment_unit="user"),
        allocation=Allocation(
            initial_canary_pct=1.0,
            ramp_schedule=[1, 5, 25, 50],
            max_autonomous_pct=50.0,
            full_rollout_requires="autonomous",
        ),
        guardrails=[
            Guardrail(
                name="error_rate",
                source="metrics.csv",
                metric="error_rate",
                threshold=Threshold(type=ThresholdType.ABSOLUTE, value=0.20),
                window=timedelta(hours=1),
                min_samples_per_arm=50,
                severity=Severity.CRITICAL,
                consecutive_breaches_to_trip=consecutive,
            )
        ],
        goal=Goal(source="metrics.csv", metric="conversion", direction="increase"),
        statistics=Statistics(
            novelty_buffer=timedelta(0),
            min_runtime=timedelta(hours=1),
            max_runtime=timedelta(days=14),
        ),
        reversion=ReversionPolicy(),
        graduation=Graduation(),
        authorization=Authorization(),
    )


def _seed(factory, *, name, state, contract, treatment_pct=0.0) -> tuple[UUID, dict[str, UUID]]:
    with session_scope(factory) as s:
        exp = ExperimentRepository(s).add(
            name=name,
            surface="checkout",
            owner="o",
            contract=contract.model_dump(mode="json"),
            spec={},
            state=state,
        )
        vr = VariantRepository(s)
        control = vr.add(experiment_id=exp.id, name="control", is_control=True, payload={})
        treatment = vr.add(experiment_id=exp.id, name="treatment", is_control=False, payload={})
        eid, vids = exp.id, {"control": control.id, "treatment": treatment.id}
    if state != State.PROPOSED.value:
        with session_scope(factory) as s:
            FlagAllocationRepository(s).upsert(
                eid, {"control": 100.0 - treatment_pct, "treatment": treatment_pct}
            )
    return eid, vids


def _sample(eid, vid, metric, n, total) -> Sample:
    # 0/1 metric, so sum_sq == sum.
    return Sample(
        experiment_id=eid,
        variant_id=vid,
        metric=metric,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        n=n,
        sum=total,
        sum_sq=total,
    )


class FakeMetrics:
    adapter_id = "metrics.fake"

    def __init__(self, samples: dict[str, list[Sample]]):
        self._samples = samples

    def supports_metric(self, metric: str) -> bool:
        return metric in self._samples

    def query(self, metric, experiment_id, variant_split_by, window_start, window_end):
        return self._samples.get(metric, [])


def _state(factory, eid) -> str:
    with session_scope(factory) as s:
        return ExperimentRepository(s).get(eid).state


def test_proposed_experiment_is_started_into_canary(factory):
    eid, _ = _seed(factory, name="prop", state=State.PROPOSED.value, contract=_contract())
    flag = PostgresFlagProvider(factory)
    outcomes = run_one_tick(
        factory,
        metrics_for=lambda exp, vids: FakeMetrics({}),
        flag=flag,
        notifier=None,
        lord=LORDController(),
        now=datetime.now(UTC),
    )
    assert ("prop", "started") in outcomes
    assert _state(factory, eid) == State.CANARY.value
    assert flag.get_allocation(eid) == {"control": 99.0, "treatment": 1.0}


def test_due_experiment_reverts_on_guardrail_and_advances_lord(factory):
    eid, vids = _seed(
        factory,
        name="reg",
        state=State.RAMPING.value,
        contract=_contract(consecutive=1),
        treatment_pct=50.0,
    )
    metrics = FakeMetrics(
        {
            # 50/50 split keeps SRM happy; treatment error rate 0.30 > the 0.20 limit.
            "conversion": [
                _sample(eid, vids["control"], "conversion", 500, 50.0),
                _sample(eid, vids["treatment"], "conversion", 500, 50.0),
            ],
            "error_rate": [
                _sample(eid, vids["control"], "error_rate", 500, 25.0),
                _sample(eid, vids["treatment"], "error_rate", 500, 150.0),
            ],
        }
    )
    lord = LORDController()
    flag = PostgresFlagProvider(factory)
    outcomes = run_one_tick(
        factory,
        metrics_for=lambda exp, v: metrics,
        flag=flag,
        notifier=None,
        lord=lord,
        now=datetime.now(UTC),
    )
    assert ("reg", "revert") in outcomes
    assert _state(factory, eid) == State.REVERTED.value
    assert flag.get_allocation(eid) == {"control": 100.0}
    assert lord.tests_seen == 1  # one terminal experiment = one LORD test


def test_not_due_experiment_is_skipped(factory):
    eid, _ = _seed(
        factory, name="busy", state=State.RAMPING.value, contract=_contract(), treatment_pct=5.0
    )
    # A decision just happened, so the experiment is not yet due to re-evaluate.
    with session_scope(factory) as s:
        DecisionRepository(s).add(
            experiment_id=eid, kind="continue", reason="recent", inputs={}, outputs={}
        )
    outcomes = run_one_tick(
        factory,
        metrics_for=lambda exp, v: FakeMetrics({}),
        flag=PostgresFlagProvider(factory),
        notifier=None,
        lord=LORDController(),
        now=datetime.now(UTC),
    )
    assert outcomes == []  # skipped: not due
    with session_scope(factory) as s:
        assert len(DecisionRepository(s).list_for(eid)) == 1  # no new decision written


def test_terminal_experiment_is_skipped(factory):
    _seed(factory, name="done", state=State.REVERTED.value, contract=_contract())
    outcomes = run_one_tick(
        factory,
        metrics_for=lambda exp, v: FakeMetrics({}),
        flag=PostgresFlagProvider(factory),
        notifier=None,
        lord=LORDController(),
        now=datetime.now(UTC),
    )
    assert outcomes == []


def test_run_executes_the_requested_number_of_ticks(factory):
    ticks = run(
        factory,
        metrics_for=lambda exp, v: FakeMetrics({}),
        flag=PostgresFlagProvider(factory),
        max_ticks=3,
        sleep=lambda seconds: None,
    )
    assert ticks == 3
