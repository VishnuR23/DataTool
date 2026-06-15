"""Scheduler — decides which experiments are due for evaluation (ARCHITECTURE.md §9.1).

The daemon runs a tick loop; on each tick the scheduler selects the experiments
that are ready to be re-evaluated and hands them to the decision engine. An
experiment is ready when it is not in a terminal state and enough time has passed
since its last decision. The cadence per experiment is
``min(its guardrail windows, the global tick interval)`` — a critical guardrail on
a one-minute window forces minute-by-minute evaluation, while a quiet experiment is
checked at the global default.

The selection is a pure function (:func:`due_experiments`) so it is testable
without real time or threads; the ``while True``/sleep loop that calls it lives in
the daemon (a later step).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from datatool.control.runtime import ExperimentRuntime
from datatool.core.models import TrustContract
from datatool.core.state_machine import is_terminal_for_scheduling

# Global default cadence (DATATOOL_TICK_INTERVAL_SECONDS; ARCHITECTURE.md §9.1, §15).
DEFAULT_TICK_INTERVAL = timedelta(seconds=60)


def tick_interval_for(
    contract: TrustContract, *, default: timedelta = DEFAULT_TICK_INTERVAL
) -> timedelta:
    """The evaluation cadence for an experiment: min(guardrail windows, default).

    A guardrail with a window shorter than the global default pulls the whole
    experiment onto that faster cadence, so a fast safety check is never starved by
    a slow global tick.
    """
    interval = default
    for guardrail in contract.guardrails:
        if guardrail.window < interval:
            interval = guardrail.window
    return interval


def is_due(
    runtime: ExperimentRuntime,
    contract: TrustContract,
    now: datetime,
    *,
    default_tick: timedelta = DEFAULT_TICK_INTERVAL,
) -> bool:
    """Whether ``runtime`` should be evaluated at ``now``.

    Terminal experiments are never due. A never-evaluated experiment is due
    immediately. Otherwise it is due once ``tick_interval`` has elapsed since the
    last decision.
    """
    if is_terminal_for_scheduling(runtime.state):
        return False
    if runtime.last_decision_at is None:
        return True
    return now >= runtime.last_decision_at + tick_interval_for(contract, default=default_tick)


def due_experiments(
    experiments: list[tuple[ExperimentRuntime, TrustContract]],
    now: datetime,
    *,
    default_tick: timedelta = DEFAULT_TICK_INTERVAL,
) -> list[ExperimentRuntime]:
    """Select the experiments ready for evaluation this tick (pure; ARCHITECTURE.md §9.1)."""
    return [
        runtime
        for runtime, contract in experiments
        if is_due(runtime, contract, now, default_tick=default_tick)
    ]
