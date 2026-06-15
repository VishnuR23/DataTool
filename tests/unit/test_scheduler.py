"""Scheduler due-selection logic (ARCHITECTURE.md §9.1).

These pin the cadence math (min of guardrail windows and the global tick) and the
readiness rules: terminal experiments are never scheduled, never-evaluated ones are
due immediately, and the rest become due once their interval has elapsed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from datatool.control.runtime import ExperimentRuntime
from datatool.control.scheduler import (
    DEFAULT_TICK_INTERVAL,
    due_experiments,
    is_due,
    tick_interval_for,
)
from datatool.core.models import Guardrail, State, Threshold, ThresholdType

from .conftest import make_contract

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _guardrail(window: timedelta, name: str = "g") -> Guardrail:
    return Guardrail(
        name=name,
        source="metrics.csv",
        metric="m",
        threshold=Threshold(type=ThresholdType.ABSOLUTE, value=1),
        window=window,
    )


def _runtime(*, state=State.RAMPING, last_decision_at=None):
    return ExperimentRuntime(
        experiment_id=uuid4(),
        name="exp",
        surface="s",
        state=state,
        current_allocation_pct=2.5,
        started_at=NOW - timedelta(days=1),
        last_decision_at=last_decision_at,
    )


# --------------------------------------------------------------------------- #
# tick_interval_for
# --------------------------------------------------------------------------- #


def test_tick_interval_defaults_to_sixty_seconds_without_fast_guardrails():
    """With only slow guardrails (>= 60s windows), the cadence is the 60s default."""
    contract = make_contract(guardrails=[_guardrail(timedelta(minutes=5))])
    assert tick_interval_for(contract) == DEFAULT_TICK_INTERVAL


def test_tick_interval_follows_a_fast_guardrail_window():
    """A guardrail window shorter than the default pulls the cadence onto it."""
    contract = make_contract(guardrails=[_guardrail(timedelta(seconds=10))])
    assert tick_interval_for(contract) == timedelta(seconds=10)


def test_tick_interval_takes_the_minimum_across_guardrails():
    """The cadence is the smallest of all guardrail windows and the default."""
    contract = make_contract(
        guardrails=[
            _guardrail(timedelta(minutes=5), "a"),
            _guardrail(timedelta(seconds=15), "b"),
        ]
    )
    assert tick_interval_for(contract) == timedelta(seconds=15)


# --------------------------------------------------------------------------- #
# is_due
# --------------------------------------------------------------------------- #


def test_never_evaluated_experiment_is_due_immediately():
    contract = make_contract()
    assert is_due(_runtime(last_decision_at=None), contract, NOW) is True


def test_recently_evaluated_experiment_is_not_due():
    contract = make_contract()  # 60s cadence
    rt = _runtime(last_decision_at=NOW - timedelta(seconds=30))
    assert is_due(rt, contract, NOW) is False


def test_experiment_becomes_due_after_its_interval():
    contract = make_contract()
    rt = _runtime(last_decision_at=NOW - timedelta(seconds=61))
    assert is_due(rt, contract, NOW) is True


def test_terminal_experiments_are_never_due():
    contract = make_contract()
    for state in (State.PROMOTED, State.REVERTED, State.CONCLUDED):
        rt = _runtime(state=state, last_decision_at=None)
        assert is_due(rt, contract, NOW) is False


def test_fast_guardrail_makes_experiment_due_sooner():
    """An experiment with a 10s guardrail is due 30s after its last decision."""
    contract = make_contract(guardrails=[_guardrail(timedelta(seconds=10))])
    rt = _runtime(last_decision_at=NOW - timedelta(seconds=30))
    assert is_due(rt, contract, NOW) is True  # 30s > 10s cadence


# --------------------------------------------------------------------------- #
# due_experiments
# --------------------------------------------------------------------------- #


def test_due_experiments_selects_only_ready_non_terminal_experiments():
    contract = make_contract()
    ready = _runtime(last_decision_at=NOW - timedelta(seconds=120))
    not_ready = _runtime(last_decision_at=NOW - timedelta(seconds=5))
    terminal = _runtime(state=State.PROMOTED, last_decision_at=None)
    fresh = _runtime(last_decision_at=None)

    selected = due_experiments(
        [(ready, contract), (not_ready, contract), (terminal, contract), (fresh, contract)],
        NOW,
    )
    ids = {rt.experiment_id for rt in selected}
    assert ready.experiment_id in ids
    assert fresh.experiment_id in ids
    assert not_ready.experiment_id not in ids
    assert terminal.experiment_id not in ids
