"""Orchestrator — turns a decision into execution-plane actions (ARCHITECTURE.md §9.3).

This is the *only* component that touches the flag provider and notification sinks,
and it is where the trust contract is enforced: every allocation is clamped to the
autonomous ceiling via ``core/contract.clamp_to_ceiling`` (the one place magnitudes
are bounded), every clamp is recorded on the action, and the ``min_step_dwell``
guard can downgrade a ramp to a hold. The orchestrator mutates the experiment's
runtime state and returns an :class:`OrchestrationResult` describing the actions
taken, the state transitions made, any trust event, and the surface cooldown — for
the caller to persist (the audit log).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from datatool.adapters.flag.base import FlagProvider
from datatool.adapters.notify.base import NotificationSink
from datatool.control.decision_engine import ControlDecision
from datatool.control.runtime import ExperimentRuntime
from datatool.core.contract import clamp_to_ceiling
from datatool.core.models import DecisionKind, State, TrustContract
from datatool.core.state_machine import transition


@dataclass
class Action:
    """One execution-plane action, mirroring the ``actions`` table (§5)."""

    kind: str  # 'allocate' | 'kill' | 'promote' | 'notify'
    adapter: str  # adapter id that performed it
    payload: dict
    clamped: bool = False
    clamp_reason: str | None = None
    succeeded: bool = True
    error: str | None = None


@dataclass
class TrustEventRecord:
    """A trust-ledger event emitted by a terminal action (§5, §9.4)."""

    surface: str
    kind: str  # 'clean_promotion' | 'false_positive_ship'
    experiment_id: UUID
    reason: str


@dataclass
class OrchestrationResult:
    """Everything the caller must persist after executing a decision."""

    actions: list[Action] = field(default_factory=list)
    transitions: list[tuple[State, State, str]] = field(default_factory=list)
    trust_event: TrustEventRecord | None = None
    cooldown_until: datetime | None = None
    halt_related: bool = False
    exclusivity_group: str | None = None


def next_ramp_step(current_pct: float, ramp_schedule: list[float]) -> float:
    """Return the next scheduled allocation above ``current_pct``.

    The schedule is strictly increasing (validated on the contract). If the current
    allocation is at or above the top of the schedule, the top is returned (the
    caller detects the no-op and holds).
    """
    for step in ramp_schedule:
        if step > current_pct:
            return float(step)
    return float(ramp_schedule[-1])


def _allocation(treatment_pct: float, variant_names: tuple[str, str]) -> dict[str, float]:
    control_name, treatment_name = variant_names
    return {control_name: 100.0 - treatment_pct, treatment_name: treatment_pct}


def execute(
    decision: ControlDecision,
    runtime: ExperimentRuntime,
    contract: TrustContract,
    *,
    flag_provider: FlagProvider,
    notifier: NotificationSink | None = None,
    now: datetime,
    variant_names: tuple[str, str] = ("control", "treatment"),
) -> OrchestrationResult:
    """Execute a decision against the execution plane, enforcing the contract.

    Mutates ``runtime`` (state, allocation, timestamps, flags, breach counters) and
    returns the actions/transitions/trust-event/cooldown to persist. Raises
    :class:`~datatool.core.exceptions.StateTransitionError` if a decision implies an
    illegal transition (a controller bug, surfaced loudly rather than hidden).
    """
    # Every cycle advances the decision clock and persists the anti-flapping state.
    runtime.last_decision_at = now
    runtime.guardrail_breach_counts = dict(decision.updated_breach_counts)

    result = OrchestrationResult()

    if decision.kind is DecisionKind.CONTINUE:
        return result  # no flag changes, no transition

    if decision.kind is DecisionKind.RAMP:
        return _execute_ramp(decision, runtime, contract, flag_provider, now, variant_names, result)

    if decision.kind is DecisionKind.PROMOTE:
        return _execute_promote(
            decision, runtime, contract, flag_provider, notifier, variant_names, result
        )

    if decision.kind is DecisionKind.REVERT:
        return _execute_revert(decision, runtime, contract, flag_provider, notifier, now, result)

    if decision.kind is DecisionKind.HOLD:
        return _execute_hold(decision, runtime, contract, flag_provider, notifier, result)

    raise AssertionError(f"unhandled decision kind: {decision.kind}")  # defensive


def _transition(
    runtime: ExperimentRuntime, target: State, reason: str, result: OrchestrationResult
) -> None:
    """Validate and apply a single state transition, recording it for the audit log."""
    if runtime.state == target:
        return  # no-op (e.g. ramping while already RAMPING)
    new_state = transition(runtime.state, target, reason)
    result.transitions.append((runtime.state, new_state, reason))
    runtime.state = new_state


def _execute_ramp(decision, runtime, contract, flag_provider, now, variant_names, result):
    desired = next_ramp_step(runtime.current_allocation_pct, contract.allocation.ramp_schedule)

    # min_step_dwell: if we ramped too recently, downgrade to a hold this cycle.
    if (
        runtime.last_ramp_at is not None
        and (now - runtime.last_ramp_at) < contract.allocation.min_step_dwell
    ):
        _transition(runtime, State.HOLDING, "min_step_dwell not satisfied; holding", result)
        return result

    # Schedule exhausted (no step above current): nothing to ramp to -> hold.
    if desired <= runtime.current_allocation_pct:
        _transition(runtime, State.HOLDING, "ramp schedule exhausted; holding", result)
        return result

    clamp = clamp_to_ceiling(desired, contract)
    allocations = _allocation(clamp.value, variant_names)
    action = Action(
        kind="allocate",
        adapter=flag_provider.adapter_id,
        payload={"allocations": allocations, "desired_pct": desired},
        clamped=clamp.clamped,
        clamp_reason=clamp.reason,
    )
    try:
        flag_provider.set_allocation(runtime.experiment_id, allocations)
    except Exception as exc:  # adapter failure: record it, do not transition
        action.succeeded = False
        action.error = str(exc)
        result.actions.append(action)
        return result

    result.actions.append(action)
    runtime.current_allocation_pct = clamp.value
    runtime.last_ramp_at = now
    _transition(runtime, State.RAMPING, "goal decisive; ramped", result)
    return result


def _execute_promote(decision, runtime, contract, flag_provider, notifier, variant_names, result):
    allocations = _allocation(100.0, variant_names)
    action = Action(
        kind="promote",
        adapter=flag_provider.adapter_id,
        payload={"allocations": allocations},
    )
    try:
        flag_provider.set_allocation(runtime.experiment_id, allocations)
    except Exception as exc:
        action.succeeded = False
        action.error = str(exc)
        result.actions.append(action)
        return result

    result.actions.append(action)
    runtime.current_allocation_pct = 100.0
    runtime.has_promoted = True
    # Pass through PROMOTING on the way to PROMOTED (the legal §10 path).
    _transition(runtime, State.PROMOTING, "promotion started", result)
    _transition(runtime, State.PROMOTED, "promoted to full rollout", result)
    result.trust_event = TrustEventRecord(
        surface=runtime.surface,
        kind="clean_promotion",
        experiment_id=runtime.experiment_id,
        reason=decision.reason,
    )
    _notify(notifier, "promote", runtime, contract, decision, result)
    return result


def _execute_revert(decision, runtime, contract, flag_provider, notifier, now, result):
    action = Action(kind="kill", adapter=flag_provider.adapter_id, payload={"kill": True})
    try:
        flag_provider.kill(runtime.experiment_id)
    except Exception as exc:
        action.succeeded = False
        action.error = str(exc)
        result.actions.append(action)
        return result

    result.actions.append(action)
    runtime.current_allocation_pct = 0.0
    _transition(runtime, State.REVERTED, decision.reason, result)

    # A revert after a prior promotion is a false-positive ship (a trust hit);
    # otherwise it is just a revert (recorded as an action, not a trust event).
    if runtime.has_promoted:
        result.trust_event = TrustEventRecord(
            surface=runtime.surface,
            kind="false_positive_ship",
            experiment_id=runtime.experiment_id,
            reason=decision.reason,
        )

    # Reversion side-effects from the contract.
    result.cooldown_until = now + contract.reversion.cooldown
    result.halt_related = contract.reversion.halt_related
    result.exclusivity_group = contract.scope.exclusivity_group
    _notify(notifier, "revert", runtime, contract, decision, result)
    return result


def _execute_hold(decision, runtime, contract, flag_provider, notifier, result):
    target = decision.target_state or State.HOLDING

    if target is State.CONCLUDED:
        # Conclude without a ship: return traffic to control, then conclude.
        action = Action(kind="kill", adapter=flag_provider.adapter_id, payload={"kill": True})
        try:
            flag_provider.kill(runtime.experiment_id)
            runtime.current_allocation_pct = 0.0
        except Exception as exc:
            action.succeeded = False
            action.error = str(exc)
        result.actions.append(action)
        _transition(runtime, State.CONCLUDED, decision.reason, result)
        return result

    _transition(runtime, State.HOLDING, decision.reason, result)

    # One-time "ready for approval" notification when held at the ceiling under a
    # human-approval rollout policy.
    at_ceiling = runtime.current_allocation_pct >= contract.allocation.max_autonomous_pct
    needs_approval = contract.allocation.full_rollout_requires == "human_approval"
    if at_ceiling and needs_approval and not runtime.approval_notified:
        runtime.approval_notified = True
        _notify(notifier, "approval_requested", runtime, contract, decision, result)
    return result


def _notify(notifier, event_kind, runtime, contract, decision, result) -> None:
    """Send a notification (if a sink is wired) and record it as an action."""
    if notifier is None:
        return
    payload = {
        "experiment": runtime.name,
        "surface": runtime.surface,
        "decision_kind": decision.kind.value,
        "reason": decision.reason,
        "channels": list(contract.reversion.notify),
    }
    action = Action(
        kind="notify", adapter=notifier.adapter_id, payload={"event": event_kind, **payload}
    )
    try:
        notifier.send(event_kind, payload)
    except Exception as exc:
        action.succeeded = False
        action.error = str(exc)
    result.actions.append(action)
