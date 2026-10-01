"""One evaluation cycle of the control loop (ARCHITECTURE.md §9).

:func:`run_cycle` is the assembly that ties the pieces together for a single
experiment at a single point in (real or simulated) time: pull the goal and
guardrail metrics from a :class:`MetricsSource`, build the observations the decision
engine consumes, decide, execute against the flag plane, and persist the full
outcome. :func:`start_experiment` performs the proposed -> canary start.

This module is shared: the simulator drives it over accelerated time, and the daemon
(a later step) will drive it on a wall-clock tick. It holds no state itself — the
caller owns the :class:`ExperimentRuntime` across cycles.

Sub-gamma bound: goal arms are built with the contract's ``goal.max_value`` (default
1.0, right for rates and proportions). A continuous goal declares its own cap; the
decision engine holds if the data provably leaves ``[0, max_value]``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from datatool.adapters.flag.base import FlagProvider
from datatool.adapters.metrics.base import CupedMetricsSource, MetricsSource
from datatool.adapters.notify.base import NotificationSink
from datatool.control.decision_engine import ControlDecision, decide
from datatool.control.orchestrator import execute
from datatool.control.persist import persist_outcome
from datatool.control.runtime import (
    AssignmentObservation,
    ExperimentRuntime,
    GoalObservation,
    GuardrailObservation,
)
from datatool.core.exceptions import CovariatesUnavailable
from datatool.core.models import Sample, State, TrustContract
from datatool.core.state_machine import is_terminal_for_scheduling, transition
from datatool.persistence.repositories import (
    AuditLogRepository,
    DecisionRepository,
    ExperimentRepository,
)
from datatool.stats.confidence_sequence import ArmStats
from datatool.stats.cuped import (
    CovariateArm,
    PrePeriodPairs,
    adjusted_max_value,
    cuped_arm,
    estimate_theta,
)


def start_experiment(
    session: Session,
    experiment,
    contract: TrustContract,
    flag: FlagProvider,
    *,
    now: datetime,
    actor: str = "system",
    variant_names: tuple[str, str] = ("control", "treatment"),
) -> State:
    """Start a proposed experiment: canary allocation + proposed -> canary."""
    control_name, treatment_name = variant_names
    canary = contract.allocation.initial_canary_pct
    flag.set_allocation(experiment.id, {control_name: 100.0 - canary, treatment_name: canary})
    new_state = transition(State(experiment.state), State.CANARY, "experiment started")
    ExperimentRepository(session).set_state(experiment.id, new_state.value)
    AuditLogRepository(session).add(
        kind="experiment.started",
        actor=actor,
        experiment_id=experiment.id,
        payload={"canary_pct": canary, "at": now.isoformat()},
    )
    return new_state


def _arm(sample: Sample | None, max_value: float) -> ArmStats:
    if sample is None or sample.n < 1:
        return ArmStats(n=0, sum=0.0, sum_sq=0.0, max_value=max_value)
    return ArmStats(n=sample.n, sum=sample.sum, sum_sq=sample.sum_sq, max_value=max_value)


def _split(samples: list[Sample], control_id: UUID, treatment_id: UUID) -> tuple:
    by_id = {s.variant_id: s for s in samples}
    return by_id.get(control_id), by_id.get(treatment_id)


def _goal_observation(
    metrics: MetricsSource,
    contract: TrustContract,
    experiment_id: UUID,
    control_id: UUID,
    treatment_id: UUID,
    window_start: datetime,
    now: datetime,
    *,
    frozen_theta: float | None = None,
) -> GoalObservation:
    fallback = None
    if contract.statistics.enable_cuped:
        if isinstance(metrics, CupedMetricsSource):
            try:
                return _cuped_goal_observation(
                    metrics,
                    contract,
                    experiment_id,
                    control_id,
                    treatment_id,
                    window_start,
                    now,
                    frozen_theta=frozen_theta,
                )
            except CovariatesUnavailable as exc:
                why = f"{metrics.adapter_id}: {exc}"
        else:
            why = f"metrics source {metrics.adapter_id} does not provide pre-period covariates"
        fallback = {
            "cuped_applied": False,
            "cuped_reason": f"{why}; plain confidence sequence used",
        }
    # The confidence sequence is cumulative, so the goal window runs from the
    # experiment's start to now.
    samples = metrics.query(
        contract.goal.metric, experiment_id, contract.scope.assignment_unit, window_start, now
    )
    control, treatment = _split(samples, control_id, treatment_id)
    bound = contract.goal.max_value  # the declared support; the engine guards it
    return GoalObservation(
        control=_arm(control, bound), treatment=_arm(treatment, bound), cuped=fallback
    )


def _cuped_goal_observation(
    metrics: CupedMetricsSource,
    contract: TrustContract,
    experiment_id: UUID,
    control_id: UUID,
    treatment_id: UUID,
    window_start: datetime,
    now: datetime,
    *,
    frozen_theta: float | None,
) -> GoalObservation:
    """Goal arms as CUPED-adjusted statistics (Deng et al. 2013; stats/cuped.py)."""
    data = metrics.query_cuped(
        contract.goal.metric,
        experiment_id,
        contract.scope.assignment_unit,
        window_start,
        now,
        contract.statistics.cuped_pre_period,
    )
    theta = frozen_theta
    if theta is None:
        theta = estimate_theta(
            PrePeriodPairs(
                n=data.pre_n,
                sum_a=data.pre_sum_a,
                sum_b=data.pre_sum_b,
                sum_aa=data.pre_sum_aa,
                sum_ab=data.pre_sum_ab,
            )
        )
    bound = contract.goal.max_value
    by_id = {arm.variant_id: arm for arm in data.arms}

    def arm(variant_id: UUID) -> ArmStats:
        moments = by_id.get(variant_id)
        if moments is None or moments.n < 1:
            return _arm(None, adjusted_max_value(theta, bound))
        return cuped_arm(
            CovariateArm(
                n=moments.n,
                sum_y=moments.sum_y,
                sum_yy=moments.sum_yy,
                sum_x=moments.sum_x,
                sum_xx=moments.sum_xx,
                sum_xy=moments.sum_xy,
            ),
            theta,
            bound,
        )

    return GoalObservation(
        control=arm(control_id),
        treatment=arm(treatment_id),
        cuped={"cuped_applied": True, "cuped_theta": theta, "cuped_pre_pairs": data.pre_n},
    )


def _frozen_cuped_theta(session: Session, experiment_id: UUID) -> float | None:
    """Theta from this experiment's earlier CUPED decisions, if any (it never moves).

    Every decision since the first CUPED one records ``cuped_theta``, so the newest
    decision carrying it is authoritative.
    """
    for decision in DecisionRepository(session).list_for(experiment_id, limit=20):
        if "cuped_theta" in decision.outputs:
            return float(decision.outputs["cuped_theta"])
    return None


def _guardrail_observation(
    metrics: MetricsSource,
    guardrail,
    experiment_id: UUID,
    control_id: UUID,
    treatment_id: UUID,
    assignment_unit: str,
    now: datetime,
) -> GuardrailObservation:
    # Guardrails use a trailing window of their own length.
    samples = metrics.query(
        guardrail.metric, experiment_id, assignment_unit, now - guardrail.window, now
    )
    control, treatment = _split(samples, control_id, treatment_id)
    return GuardrailObservation(
        control_value=(control.sum / control.n) if control and control.n else 0.0,
        treatment_value=(treatment.sum / treatment.n) if treatment and treatment.n else 0.0,
        n_control=control.n if control else 0,
        n_treatment=treatment.n if treatment else 0,
    )


def run_cycle(
    session: Session,
    runtime: ExperimentRuntime,
    contract: TrustContract,
    *,
    metrics: MetricsSource,
    flag: FlagProvider,
    variant_ids: dict[str, UUID],
    control_name: str,
    treatment_name: str,
    now: datetime,
    goal_alpha: float,
    started_at: datetime | None = None,
    evaluate_srm: bool = True,
    notifier: NotificationSink | None = None,
    actor: str = "system",
) -> ControlDecision | None:
    """Run one decision cycle for ``runtime``, persisting the outcome.

    Returns the :class:`ControlDecision` made, or ``None`` if the experiment is in a
    terminal state (nothing to do). Mutates ``runtime`` via the orchestrator. The
    caller supplies the variant name->id map and which names are control/treatment.

    ``evaluate_srm`` controls the sample-ratio-mismatch check. It is meaningful only
    when assignments actually follow the controller's allocation (the live daemon).
    In replay the historical split is fixed and given, so the simulator passes
    ``evaluate_srm=False`` to avoid a spurious mismatch against a ramping allocation.
    """
    if is_terminal_for_scheduling(runtime.state):
        return None

    control_id = variant_ids[control_name]
    treatment_id = variant_ids[treatment_name]
    window_start = started_at or runtime.started_at

    frozen_theta = (
        _frozen_cuped_theta(session, runtime.experiment_id)
        if contract.statistics.enable_cuped
        else None
    )
    goal = _goal_observation(
        metrics,
        contract,
        runtime.experiment_id,
        control_id,
        treatment_id,
        window_start,
        now,
        frozen_theta=frozen_theta,
    )
    guardrails = {
        guardrail.name: _guardrail_observation(
            metrics,
            guardrail,
            runtime.experiment_id,
            control_id,
            treatment_id,
            contract.scope.assignment_unit,
            now,
        )
        for guardrail in contract.guardrails
        if metrics.supports_metric(guardrail.metric)
    }

    assignments: AssignmentObservation | None = None
    if evaluate_srm:
        treatment_share = runtime.current_allocation_pct / 100.0
        assignments = AssignmentObservation(
            counts={control_name: goal.control.n, treatment_name: goal.treatment.n},
            expected_ratios={control_name: 1.0 - treatment_share, treatment_name: treatment_share},
        )

    decision = decide(
        runtime,
        contract,
        now=now,
        goal=goal,
        guardrails=guardrails,
        assignments=assignments,
        goal_alpha=goal_alpha,
    )
    if goal.cuped:
        # Recorded on every decision: `why` explains it, and the next cycle reads the
        # frozen theta back from here.
        decision.structured_reason.update(goal.cuped)
    result = execute(
        decision,
        runtime,
        contract,
        flag_provider=flag,
        notifier=notifier,
        now=now,
        variant_names=(control_name, treatment_name),
    )
    persist_outcome(
        session, runtime.experiment_id, decision, result, adapter_id=flag.adapter_id, actor=actor
    )
    return decision
