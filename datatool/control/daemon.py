"""The control-plane daemon (ARCHITECTURE.md §9.1).

:func:`run_one_tick` is one pass of the control loop over every experiment: start the
proposed ones, evaluate the due running ones via :func:`run_cycle`, and advance the
org-wide online-FDR (LORD) controller when an experiment concludes. :func:`run` wraps
it in a sleep loop. The per-tick work is the testable unit; the loop is thin.

LORD: the per-experiment goal level is ``fdr_budget_share * lord.next_alpha()`` and
the controller records one outcome per experiment that reaches a terminal state
(ARCHITECTURE.md §8.2). Each tick first resyncs the controller from the audit log
(``loader.sync_lord_from_history``), so restarts and manual promotes/reverts are
accounted for. This is deliberately strict — stacking the conservative
confidence sequence with online-FDR control means live experiments need substantial
data to ship, which is the org-wide false-discovery guarantee working as intended.

The read-only HTTP API and ``/metrics`` are served alongside this loop by
``datatool daemon`` (see ``cli/commands.py``), not from here.

Graduation rules (§9.4) run when a promote or revert is persisted
(``control/persist.py``), so they apply here and to the manual CLI commands alike.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from datatool.adapters.flag.base import FlagProvider
from datatool.adapters.metrics.base import MetricsSource
from datatool.adapters.notify.base import NotificationSink
from datatool.control.loader import (
    load_effective_contract,
    load_runtime,
    sync_lord_from_history,
)
from datatool.control.loop import run_cycle, start_experiment
from datatool.control.scheduler import is_due
from datatool.core.models import DecisionKind, State
from datatool.core.state_machine import is_terminal_for_scheduling
from datatool.observability.logging import get_logger
from datatool.observability.metrics import (
    DECISIONS_TOTAL,
    EXPERIMENTS_ACTIVE,
    GUARDRAIL_EVALUATIONS_TOTAL,
    LOOP_DURATION_SECONDS,
)
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import ExperimentRepository, VariantRepository
from datatool.stats.fdr import LORDController

# Resolve a metrics source for an experiment, given its variant name->id mapping.
MetricsResolver = Callable[[object, dict[str, UUID]], MetricsSource]

_log = get_logger("daemon")


def run_one_tick(
    session_factory: sessionmaker[Session],
    *,
    metrics_for: MetricsResolver,
    flag: FlagProvider,
    notifier: NotificationSink | None,
    lord: LORDController,
    now: datetime,
    actor: str = "daemon",
) -> list[tuple[str, str]]:
    """One control-loop pass. Returns (experiment_name, outcome) for what it touched."""
    with LOOP_DURATION_SECONDS.time():
        with session_scope(session_factory) as session:
            experiments = [(e.id, e.name) for e in ExperimentRepository(session).list()]
            # The persisted history is authoritative: survives restarts and counts
            # experiments concluded by manual commands in other processes.
            sync_lord_from_history(session, lord)

        outcomes: list[tuple[str, str]] = []
        for experiment_id, name in experiments:
            # Isolate each experiment: one broken metrics source or contract must not
            # starve the rest of the tick — least of all another experiment's
            # guardrail revert. Its session rolls back, so nothing partial persists,
            # and it is retried on the next tick.
            try:
                outcome = _process_experiment(
                    session_factory,
                    experiment_id,
                    metrics_for=metrics_for,
                    flag=flag,
                    notifier=notifier,
                    lord=lord,
                    now=now,
                    actor=actor,
                )
            except Exception:
                _log.exception("experiment.tick_failed", experiment=name)
                outcome = (name, "error")
            if outcome is not None:
                outcomes.append(outcome)

        _refresh_active_gauge(session_factory)
        return outcomes


def _process_experiment(
    session_factory: sessionmaker[Session],
    experiment_id: UUID,
    *,
    metrics_for: MetricsResolver,
    flag: FlagProvider,
    notifier: NotificationSink | None,
    lord: LORDController,
    now: datetime,
    actor: str,
) -> tuple[str, str] | None:
    with session_scope(session_factory) as session:
        experiment = ExperimentRepository(session).get(experiment_id)
        state = State(experiment.state)
        if is_terminal_for_scheduling(state):
            return None

        contract = load_effective_contract(session, experiment)

        if state is State.PROPOSED:
            start_experiment(session, experiment, contract, flag, now=now, actor=actor)
            _log.info("experiment.started", experiment=experiment.name, surface=experiment.surface)
            return (experiment.name, "started")

        runtime = load_runtime(session, experiment)
        if not is_due(runtime, contract, now):
            return None

        variants = VariantRepository(session).list_for(experiment.id)
        control = next(v for v in variants if v.is_control)
        treatment = next(v for v in variants if not v.is_control)
        variant_ids = {v.name: v.id for v in variants}

        metrics = metrics_for(experiment, variant_ids)
        goal_alpha = contract.statistics.fdr_budget_share * lord.next_alpha()

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
            goal_alpha=goal_alpha,
            evaluate_srm=True,
            notifier=notifier,
            actor=actor,
        )

        DECISIONS_TOTAL.labels(kind=decision.kind.value, surface=experiment.surface).inc()
        for ev in decision.guardrail_evaluations:
            GUARDRAIL_EVALUATIONS_TOTAL.labels(
                guardrail=ev.guardrail_name, breached=str(ev.breached), severity=ev.severity.value
            ).inc()

        # One LORD test per experiment that concludes; a promotion is a discovery.
        if is_terminal_for_scheduling(runtime.state):
            lord.record_outcome(decision.kind is DecisionKind.PROMOTE)
            _log.info(
                "experiment.concluded",
                experiment=experiment.name,
                final_state=runtime.state.value,
                decision=decision.kind.value,
            )

        return (experiment.name, decision.kind.value)


def _refresh_active_gauge(session_factory: sessionmaker[Session]) -> None:
    counts: dict[str, int] = {}
    with session_scope(session_factory) as session:
        for experiment in ExperimentRepository(session).list():
            counts[experiment.state] = counts.get(experiment.state, 0) + 1
    for state in State:
        EXPERIMENTS_ACTIVE.labels(state=state.value).set(counts.get(state.value, 0))


def run(
    session_factory: sessionmaker[Session],
    *,
    metrics_for: MetricsResolver,
    flag: FlagProvider,
    notifier: NotificationSink | None = None,
    lord: LORDController | None = None,
    tick_interval_seconds: int = 60,
    max_ticks: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run the control loop until stopped. Returns the number of ticks executed.

    ``max_ticks`` (and an injectable ``sleep``) make the loop testable; the daemon
    CLI leaves ``max_ticks=None`` to run until interrupted.
    """
    lord = lord or LORDController()
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        try:
            run_one_tick(
                session_factory,
                metrics_for=metrics_for,
                flag=flag,
                notifier=notifier,
                lord=lord,
                now=datetime.now(UTC),
            )
        except Exception:
            _log.exception("daemon.tick_failed")
        ticks += 1
        if max_ticks is not None and ticks >= max_ticks:
            break
        sleep(tick_interval_seconds)
    return ticks
