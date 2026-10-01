"""Decision engine — the controller's brain (ARCHITECTURE.md §9.2).

Given an experiment's running state and one cycle of observations, :func:`decide`
produces a single :class:`ControlDecision`: what the controller wants to do and
which state to move to. It is a **pure function** — no I/O, no persistence, no flag
changes — which is what makes the controller's judgement auditable and exhaustively
testable. Carrying out the decision (clamping, flag calls, notifications, state
transitions) is the orchestrator's job (§9.3).

The evaluation order is exactly §9.2, and it is an order that fails safe:

1. Hard pre-checks, most decisive first:
   - past ``max_runtime``      -> conclude without shipping
   - inside ``novelty_buffer`` -> continue (suppress early decisions)
   - sample ratio mismatch     -> revert (the data is untrustworthy)
2. Guardrails (the fast safety loop): a tripped critical/high guardrail reverts
   immediately; a tripped medium guardrail forces a hold.
3. Goal evaluation (sequential stats): the confidence sequence on the goal metric
   drives ramp / hold / promote / revert / continue.

Only after a clean SRM check and no tripped guardrail does the engine look at the
goal — safety always precedes optimization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from datatool.control.runtime import (
    AssignmentObservation,
    ExperimentRuntime,
    GoalObservation,
    GuardrailObservation,
)
from datatool.core.exceptions import StatisticsError
from datatool.core.models import Decision, DecisionKind, State, TrustContract
from datatool.stats.confidence_sequence import ArmStats, confidence_sequence_diff
from datatool.stats.guardrails import GuardrailEvaluation, evaluate_guardrail
from datatool.stats.srm import SRM_P_VALUE_THRESHOLD, srm_p_value


@dataclass
class ControlDecision:
    """The decision engine's output for one cycle.

    ``kind`` is the persisted :class:`DecisionKind` (one of the five in §6).
    ``target_state`` is the lifecycle state to move to, or ``None`` to stay put.
    ``updated_breach_counts`` carries the new anti-flapping counters the caller must
    persist; ``guardrail_evaluations`` are recorded to the audit log.
    """

    kind: DecisionKind
    target_state: State | None
    reason: str
    structured_reason: dict
    target_allocation_pct: float | None = None  # ramp/promote target (pre-clamp)
    guardrail_evaluations: list[GuardrailEvaluation] = field(default_factory=list)
    updated_breach_counts: dict[str, int] = field(default_factory=dict)

    def to_decision(self, experiment_id: UUID) -> Decision:
        """Project onto the persisted :class:`~datatool.core.models.Decision` model."""
        suggested: dict | None = None
        if self.kind is DecisionKind.RAMP:
            suggested = {"action": "ramp_to_next_step"}
        elif self.kind is DecisionKind.PROMOTE:
            suggested = {"action": "promote", "to_pct": self.target_allocation_pct}
        elif self.kind is DecisionKind.REVERT:
            suggested = {"action": "kill"}
        return Decision(
            experiment_id=experiment_id,
            kind=self.kind,
            reason=self.reason,
            structured_reason=self.structured_reason,
            suggested_action=suggested,
        )


def _outside_support(arm: ArmStats) -> bool:
    """Whether the aggregates prove some observation lies outside [0, max_value].

    Every x in [0, c] satisfies x <= c and x**2 <= c * x, so in aggregate
    0 <= sum <= c * n and sum_sq <= c * sum. Breaking either is proof of an
    out-of-support value; passing is necessary, not sufficient (a few values just
    above c can hide among many small ones), so this guards rather than certifies.
    """
    c = arm.max_value
    tol = 1e-9 * max(1.0, abs(arm.sum), abs(arm.sum_sq))  # float accumulation noise
    return arm.sum < -tol or arm.sum > c * arm.n + tol or arm.sum_sq > c * arm.sum + tol


def decide(
    runtime: ExperimentRuntime,
    contract: TrustContract,
    *,
    now: datetime,
    goal: GoalObservation | None,
    guardrails: dict[str, GuardrailObservation],
    assignments: AssignmentObservation | None,
    goal_alpha: float,
    min_assignments_for_srm: int = 100,
) -> ControlDecision:
    """Decide what to do with one experiment this cycle (pure; ARCHITECTURE.md §9.2).

    ``goal_alpha`` is the effective per-experiment level for the confidence
    sequence — typically ``contract.statistics.fdr_budget_share * lord.next_alpha()``
    computed by the caller (§8.2). ``guardrails`` maps each contract guardrail's
    name to its measured values this cycle (a guardrail with no entry is skipped).
    """
    elapsed = now - runtime.started_at
    # Counters start from the prior cycle; a branch that does not touch guardrails
    # carries them forward unchanged.
    carried_counts = dict(runtime.guardrail_breach_counts)

    # --- 1. Hard pre-checks ------------------------------------------------- #

    # max_runtime: conclude without shipping. DECISION: persisted DecisionKind has
    # no "conclude", so we record the closest kind (HOLD — stop, no ship) and carry
    # the terminal meaning in target_state=CONCLUDED and the reason string.
    if elapsed >= contract.statistics.max_runtime:
        return ControlDecision(
            kind=DecisionKind.HOLD,
            target_state=State.CONCLUDED,
            reason="max_runtime reached without a decisive result; concluding without a ship",
            structured_reason={
                "elapsed_seconds": elapsed.total_seconds(),
                "max_runtime_seconds": contract.statistics.max_runtime.total_seconds(),
            },
            updated_breach_counts=carried_counts,
        )

    # novelty_buffer: suppress all decisions during the initial settling window.
    if elapsed < contract.statistics.novelty_buffer:
        return ControlDecision(
            kind=DecisionKind.CONTINUE,
            target_state=None,
            reason="within novelty buffer; suppressing decisions",
            structured_reason={
                "elapsed_seconds": elapsed.total_seconds(),
                "novelty_buffer_seconds": contract.statistics.novelty_buffer.total_seconds(),
            },
            updated_breach_counts=carried_counts,
        )

    # SRM: a mismatch means the assignment data is untrustworthy -> revert. Only
    # evaluated once enough units have accrued for the chi-squared test to be
    # meaningful, and skipped (not errored) when it is structurally inapplicable.
    if assignments is not None and sum(assignments.counts.values()) >= min_assignments_for_srm:
        try:
            p = srm_p_value(assignments.counts, assignments.expected_ratios)
        except StatisticsError:
            p = None
        if p is not None and p < SRM_P_VALUE_THRESHOLD:
            return ControlDecision(
                kind=DecisionKind.REVERT,
                target_state=State.REVERTED,
                reason="srm_failed",
                structured_reason={
                    "srm_p_value": p,
                    "srm_threshold": SRM_P_VALUE_THRESHOLD,
                    "observed_counts": dict(assignments.counts),
                    "expected_ratios": dict(assignments.expected_ratios),
                },
                updated_breach_counts=carried_counts,
            )

    # --- 2. Guardrails (fast loop) ----------------------------------------- #

    evaluations: list[GuardrailEvaluation] = []
    updated_counts = dict(runtime.guardrail_breach_counts)
    medium_trip: GuardrailEvaluation | None = None

    for guardrail in contract.guardrails:
        obs = guardrails.get(guardrail.name)
        if obs is None:
            continue  # no measurement for this guardrail this cycle
        ev = evaluate_guardrail(
            guardrail,
            control_value=obs.control_value,
            treatment_value=obs.treatment_value,
            n_control=obs.n_control,
            n_treatment=obs.n_treatment,
            prior_consecutive_breaches=runtime.guardrail_breach_counts.get(guardrail.name, 0),
        )
        evaluations.append(ev)
        updated_counts[guardrail.name] = ev.consecutive_breaches

        if ev.tripped and ev.suggested_decision is DecisionKind.REVERT:
            return ControlDecision(
                kind=DecisionKind.REVERT,
                target_state=State.REVERTED,
                reason=f"guardrail.{guardrail.name}.tripped",
                structured_reason={
                    "guardrail": guardrail.name,
                    "severity": guardrail.severity.value,
                    "value": ev.value,
                    "threshold": ev.threshold,
                    "consecutive_breaches": ev.consecutive_breaches,
                },
                guardrail_evaluations=evaluations,
                updated_breach_counts=updated_counts,
            )
        if ev.tripped and ev.suggested_decision is DecisionKind.HOLD:
            medium_trip = ev  # remember; a medium trip holds rather than reverts

    if medium_trip is not None:
        return ControlDecision(
            kind=DecisionKind.HOLD,
            target_state=State.HOLDING,
            reason=f"guardrail.{medium_trip.guardrail_name}.tripped (medium); holding",
            structured_reason={
                "guardrail": medium_trip.guardrail_name,
                "severity": medium_trip.severity.value,
                "value": medium_trip.value,
                "threshold": medium_trip.threshold,
            },
            guardrail_evaluations=evaluations,
            updated_breach_counts=updated_counts,
        )

    # --- 3. Goal evaluation (sequential stats) ----------------------------- #

    if goal is None or goal.control.n < 1 or goal.treatment.n < 1:
        return ControlDecision(
            kind=DecisionKind.CONTINUE,
            target_state=None,
            reason="insufficient goal data; continuing",
            structured_reason={"goal_alpha": goal_alpha},
            guardrail_evaluations=evaluations,
            updated_breach_counts=updated_counts,
        )

    # The CS assumes every observation lies in [0, max_value] (Howard et al. 2021,
    # sub-gamma with scale c = max_value). Data that provably breaks that would
    # yield bounds without coverage, so hold rather than decide on them.
    offending = [
        name
        for name, arm in (("control", goal.control), ("treatment", goal.treatment))
        if _outside_support(arm)
    ]
    if offending:
        bound = goal.treatment.max_value
        return ControlDecision(
            kind=DecisionKind.HOLD,
            target_state=State.HOLDING,
            reason=(
                f"goal metric {contract.goal.metric!r} has values outside [0, {bound:g}] "
                f"({', '.join(offending)}); the confidence sequence assumes that support, "
                "so holding instead of deciding"
            ),
            structured_reason={
                "goal_alpha": goal_alpha,
                "support_violation": offending,
                "assumed_support": [0.0, bound],
            },
            guardrail_evaluations=evaluations,
            updated_breach_counts=updated_counts,
        )

    cs = confidence_sequence_diff(goal.control, goal.treatment, alpha=goal_alpha)
    direction = contract.goal.direction
    if direction == "increase":
        winning, losing = cs.lower > 0, cs.upper < 0
    else:  # "decrease": a lower treatment mean is the win
        winning, losing = cs.upper < 0, cs.lower > 0

    structured = {
        "cs_lower": float(cs.lower),
        "cs_upper": float(cs.upper),
        "cs_point_estimate": float(cs.point_estimate),
        "goal_alpha": goal_alpha,
        "goal_direction": direction,
        "n_control": goal.control.n,
        "n_treatment": goal.treatment.n,
        "current_allocation_pct": runtime.current_allocation_pct,
    }

    if losing:
        return ControlDecision(
            kind=DecisionKind.REVERT,
            target_state=State.REVERTED,
            reason="goal.lost",
            structured_reason=structured,
            guardrail_evaluations=evaluations,
            updated_breach_counts=updated_counts,
        )

    if winning:
        ceiling = contract.allocation.max_autonomous_pct
        if runtime.current_allocation_pct < ceiling:
            return ControlDecision(
                kind=DecisionKind.RAMP,
                target_state=State.RAMPING,
                reason="goal confidence sequence is decisive; ramping toward the ceiling",
                structured_reason=structured,
                guardrail_evaluations=evaluations,
                updated_breach_counts=updated_counts,
            )
        # At/above the autonomous ceiling: only ship autonomously if the contract
        # allows it AND the minimum runtime floor has elapsed; otherwise hold.
        autonomous = contract.allocation.full_rollout_requires == "autonomous"
        if autonomous and elapsed >= contract.statistics.min_runtime:
            return ControlDecision(
                kind=DecisionKind.PROMOTE,
                target_state=State.PROMOTING,
                reason="goal decisive at the autonomous ceiling; promoting to full rollout",
                structured_reason=structured,
                target_allocation_pct=100.0,
                guardrail_evaluations=evaluations,
                updated_breach_counts=updated_counts,
            )
        hold_reason = (
            "goal decisive at the ceiling; awaiting human approval for full rollout"
            if not autonomous
            else "goal decisive at the ceiling; holding until min_runtime elapses"
        )
        return ControlDecision(
            kind=DecisionKind.HOLD,
            target_state=State.HOLDING,
            reason=hold_reason,
            structured_reason=structured,
            guardrail_evaluations=evaluations,
            updated_breach_counts=updated_counts,
        )

    return ControlDecision(
        kind=DecisionKind.CONTINUE,
        target_state=None,
        reason="goal confidence sequence still spans zero; continuing",
        structured_reason=structured,
        guardrail_evaluations=evaluations,
        updated_breach_counts=updated_counts,
    )
