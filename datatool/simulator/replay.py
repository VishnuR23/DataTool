"""Replay a historical CSV through the controller (ARCHITECTURE.md §14).

Point :func:`simulate` at a registered (proposed) experiment and a CSV of events and
it drives the real control loop over simulated time — starting the canary, then
ticking forward, feeding each cycle the goal (cumulative) and guardrail (windowed)
metrics from the CSV adapter — and returns every decision the controller would have
made. This is the adoption tool: run last quarter's data and see what DataTool would
have done.

SRM is not evaluated in replay: the historical split is fixed and given, so comparing
it to the controller's (ramping) allocation would be a spurious mismatch
(see ``control/loop.run_cycle``'s ``evaluate_srm``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from datatool.adapters.flag.postgres import PostgresFlagProvider
from datatool.adapters.metrics.csv import CsvMetricsSource
from datatool.control.loader import load_effective_contract
from datatool.control.loop import run_cycle, start_experiment
from datatool.control.runtime import ExperimentRuntime
from datatool.control.scheduler import tick_interval_for
from datatool.core.exceptions import DataToolError
from datatool.core.models import DecisionKind, State
from datatool.core.state_machine import is_terminal_for_scheduling
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import ExperimentRepository, VariantRepository

_MAX_CYCLES = 150  # bound the number of evaluation cycles over the data span


@dataclass
class SimulationStep:
    """A noteworthy moment in the replay (a non-continue decision)."""

    at: datetime
    decision_kind: str
    state: str
    allocation_pct: float
    reason: str


@dataclass
class SimulationResult:
    experiment: str
    final_state: State
    final_allocation_pct: float
    n_cycles: int
    steps: list[SimulationStep] = field(default_factory=list)


class SimulationError(DataToolError):
    """The experiment or data could not be simulated."""


def _default_step(contract, start: datetime, end: datetime) -> timedelta:
    total = end - start
    tick = tick_interval_for(contract)
    if total <= timedelta(0):
        return tick
    if total / tick > _MAX_CYCLES:
        return total / _MAX_CYCLES
    return tick


def simulate(
    session_factory: sessionmaker[Session],
    identifier_name: str,
    csv_path: str,
    *,
    speed: str | None = None,  # advisory; replay is deterministic and runs instantly
    step: timedelta | None = None,
    goal_alpha: float | None = None,
    actor: str = "simulator",
) -> SimulationResult:
    """Replay ``csv_path`` through the experiment named ``identifier_name``."""
    with session_scope(session_factory) as session:
        experiment = ExperimentRepository(session).get_by_name(identifier_name)
        if experiment is None:
            raise SimulationError(f"no experiment named {identifier_name!r}")
        if experiment.state != State.PROPOSED.value:
            raise SimulationError(
                f"{identifier_name} is {experiment.state}; simulate a freshly registered "
                f"(proposed) experiment"
            )
        variants = VariantRepository(session).list_for(experiment.id)
        control = next(v for v in variants if v.is_control)
        treatment = next(v for v in variants if not v.is_control)
        variant_ids = {v.name: v.id for v in variants}
        contract = load_effective_contract(session, experiment)
        experiment_id, surface, name = experiment.id, experiment.surface, experiment.name

    metrics = CsvMetricsSource(csv_path, variant_ids)
    span = metrics.time_span()
    if span is None:
        raise SimulationError(f"no events in {csv_path}")
    span_start, span_end = span
    flag = PostgresFlagProvider(session_factory)

    with session_scope(session_factory) as session:
        experiment = ExperimentRepository(session).get(experiment_id)
        start_experiment(
            session,
            experiment,
            contract,
            flag,
            now=span_start,
            actor=actor,
            variant_names=(control.name, treatment.name),
        )

    runtime = ExperimentRuntime(
        experiment_id=experiment_id,
        name=name,
        surface=surface,
        state=State.CANARY,
        current_allocation_pct=contract.allocation.initial_canary_pct,
        started_at=span_start,
    )
    alpha = goal_alpha if goal_alpha is not None else contract.statistics.alpha
    tick = step or _default_step(contract, span_start, span_end)

    steps: list[SimulationStep] = []
    now = span_start
    n_cycles = 0
    while now <= span_end:
        with session_scope(session_factory) as session:
            decision = run_cycle(
                session,
                runtime,
                contract,
                metrics=metrics,
                flag=flag,
                variant_ids=variant_ids,
                control_name=control.name,
                treatment_name=treatment.name,
                now=now,
                goal_alpha=alpha,
                started_at=span_start,
                evaluate_srm=False,
                actor=actor,
            )
        n_cycles += 1
        if decision is not None and decision.kind is not DecisionKind.CONTINUE:
            steps.append(
                SimulationStep(
                    at=now,
                    decision_kind=decision.kind.value,
                    state=runtime.state.value,
                    allocation_pct=runtime.current_allocation_pct,
                    reason=decision.reason,
                )
            )
        if is_terminal_for_scheduling(runtime.state):
            break
        now += tick

    return SimulationResult(
        experiment=name,
        final_state=runtime.state,
        final_allocation_pct=runtime.current_allocation_pct,
        n_cycles=n_cycles,
        steps=steps,
    )
