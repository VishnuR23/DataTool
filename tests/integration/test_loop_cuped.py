"""CUPED in the control loop (ARCHITECTURE.md §8.5).

With ``statistics.enable_cuped`` the goal arms fed to the confidence sequence are the
CUPED-adjusted ones, theta is frozen at the experiment's first CUPED decision, and
every decision records what was done (``cuped_*`` keys) — including why CUPED was
not applied when the metrics source cannot provide covariates.
"""

from __future__ import annotations

from datetime import timedelta

from datatool.control.loop import run_cycle
from datatool.control.runtime import ExperimentRuntime
from datatool.core.models import CovariateSample, CupedData, State, Statistics
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import DecisionRepository

from .test_loop import START, FakeFlag, _contract, _seed


def _cuped_contract():
    contract = _contract()
    stats = Statistics(
        novelty_buffer=timedelta(0),
        min_runtime=timedelta(hours=1),
        enable_cuped=True,
        cuped_pre_period=timedelta(days=7),
    )
    return contract.model_copy(update={"statistics": stats})


class FakeCupedMetrics:
    """Serves CUPED data; each call can report different pre-period pairs."""

    adapter_id = "metrics.fake_cuped"

    def __init__(self, vids, pre_slopes):
        self._vids = vids
        self._slopes = list(pre_slopes)
        self.pre_periods = []

    def supports_metric(self, metric):
        return True

    def query(self, metric, experiment_id, unit, start, end):
        return []

    def query_cuped(self, metric, experiment_id, unit, start, end, pre_period):
        self.pre_periods.append(pre_period)
        slope = self._slopes.pop(0)
        arm = dict(n=400, sum_y=80.0, sum_yy=80.0, sum_x=60.0, sum_xx=20.0, sum_xy=30.0)
        return CupedData(
            arms=[CovariateSample(variant_id=v, **arm) for v in self._vids.values()],
            # Pairs (a, slope * a) for a in {0, 0.5, 1}: theta == slope.
            pre_n=3,
            pre_sum_a=1.5,
            pre_sum_b=1.5 * slope,
            pre_sum_aa=1.25,
            pre_sum_ab=1.25 * slope,
        )


class PlainMetrics:
    adapter_id = "metrics.plain"

    def supports_metric(self, metric):
        return True

    def query(self, metric, experiment_id, unit, start, end):
        return []


def _cycle(session_factory, eid, vids, contract, metrics, minutes):
    runtime = ExperimentRuntime(
        experiment_id=eid,
        name="sim",
        surface="x",
        state=State.CANARY,
        current_allocation_pct=1.0,
        started_at=START,
    )
    with session_scope(session_factory) as s:
        return run_cycle(
            s,
            runtime,
            contract,
            metrics=metrics,
            flag=FakeFlag(),
            variant_ids=vids,
            control_name="control",
            treatment_name="treatment",
            now=START + timedelta(minutes=minutes),
            goal_alpha=0.05,
            started_at=START,
            evaluate_srm=False,
        )


def test_cuped_arms_feed_the_cs_and_theta_is_recorded(session_factory):
    eid, vids = _seed(session_factory)
    metrics = FakeCupedMetrics(vids, pre_slopes=[0.5])
    decision = _cycle(session_factory, eid, vids, _cuped_contract(), metrics, minutes=90)

    assert metrics.pre_periods == [timedelta(days=7)]
    reason = decision.structured_reason
    assert reason["cuped_applied"] is True
    assert reason["cuped_theta"] == 0.5
    assert reason["cuped_pre_pairs"] == 3
    assert "cs_lower" in reason  # the CS ran on the adjusted arms


def test_theta_is_frozen_at_the_first_cuped_decision(session_factory):
    """Late-arriving pre-period data must not move theta mid-experiment."""
    eid, vids = _seed(session_factory)
    metrics = FakeCupedMetrics(vids, pre_slopes=[0.5, 0.9])
    contract = _cuped_contract()
    _cycle(session_factory, eid, vids, contract, metrics, minutes=90)
    second = _cycle(session_factory, eid, vids, contract, metrics, minutes=120)
    assert second.structured_reason["cuped_theta"] == 0.5


def test_a_source_without_covariates_falls_back_and_says_why(session_factory):
    eid, vids = _seed(session_factory)
    decision = _cycle(session_factory, eid, vids, _cuped_contract(), PlainMetrics(), minutes=90)
    reason = decision.structured_reason
    assert reason["cuped_applied"] is False
    assert "metrics.plain" in reason["cuped_reason"]


def test_cuped_off_leaves_no_trace(session_factory):
    eid, vids = _seed(session_factory)
    decision = _cycle(session_factory, eid, vids, _contract(), PlainMetrics(), minutes=90)
    assert not any(key.startswith("cuped_") for key in decision.structured_reason)
    with session_scope(session_factory) as s:
        assert len(DecisionRepository(s).list_for(eid)) == 1
