from datatool.console.live import FeedPoller, experiment_summaries
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import ActionRepository, ExperimentRepository


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
