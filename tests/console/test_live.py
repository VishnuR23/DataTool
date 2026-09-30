from datatool.console.live import FeedPoller, experiment_summaries, goal_trace
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import (
    ActionRepository,
    DecisionRepository,
    ExperimentRepository,
)


def _experiment(s, name="exp"):
    return ExperimentRepository(s).add(
        name=name, surface="checkout", owner="o", contract={}, spec={}, state="ramping"
    )


def _action(s, exp_id):
    ActionRepository(s).add(
        experiment_id=exp_id,
        decision_id=None,
        kind="promote",
        adapter="postgres",
        payload={},
        clamped=False,
        clamp_reason=None,
        succeeded=True,
        error=None,
    )


def test_poll_returns_new_events_then_nothing_until_more_arrive(session_factory):
    with session_scope(session_factory) as s:
        exp = _experiment(s)
        _action(s, exp.id)

    poller = FeedPoller(session_factory)
    first = poller.poll()
    assert any(e.kind == "promote" for e in first)

    # Nothing new → empty, no duplicates from the inclusive watermark re-read.
    assert poller.poll() == []

    with session_scope(session_factory) as s:
        exp = ExperimentRepository(s).get_by_name("exp")
        _action(s, exp.id)
    third = poller.poll()
    assert len(third) == 1 and third[0].kind == "promote"


def test_experiment_summaries_reflect_state(session_factory):
    with session_scope(session_factory) as s:
        _experiment(s, name="pricing")
    summaries = experiment_summaries(session_factory)
    assert len(summaries) == 1
    assert summaries[0].name == "pricing"
    assert summaries[0].state == "ramping"
    assert summaries[0].surface == "checkout"


def _decision(s, exp_id, outputs, kind="continue", reason="r"):
    DecisionRepository(s).add(
        experiment_id=exp_id, kind=kind, reason=reason, inputs={}, outputs=outputs
    )


def _cs(lower, estimate, upper, pct=10.0):
    return {
        "cs_lower": lower,
        "cs_point_estimate": estimate,
        "cs_upper": upper,
        "goal_alpha": 0.05,
        "goal_direction": "increase",
        "n_control": 100,
        "n_treatment": 101,
        "current_allocation_pct": pct,
    }


def test_goal_trace_is_chronological_and_skips_cycles_without_goal_stats(session_factory):
    with session_scope(session_factory) as s:
        exp = ExperimentRepository(s).add(
            name="pricing",
            surface="checkout",
            owner="o",
            contract={"goal": {"metric": "conversion", "direction": "increase"}},
            spec={},
            state="ramping",
        )
        _decision(s, exp.id, {"goal_alpha": 0.05}, reason="insufficient goal data")
        _decision(s, exp.id, _cs(-0.05, 0.01, 0.07))
        _decision(s, exp.id, _cs(0.002, 0.03, 0.06, pct=25.0), kind="ramp", reason="ramping")

    trace = goal_trace(session_factory, "pricing")

    assert trace.metric == "conversion"
    assert [p.estimate for p in trace.points] == [0.01, 0.03]  # oldest first
    assert trace.points[-1].lower == 0.002 and trace.points[-1].upper == 0.06
    assert trace.latest_reason == "ramping"  # the newest decision, stats or not
    assert trace.latest_outputs["current_allocation_pct"] == 25.0


def test_goal_trace_of_unknown_experiment_is_none(session_factory):
    assert goal_trace(session_factory, "missing") is None
