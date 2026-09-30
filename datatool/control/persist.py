"""Persist a decision + its execution outcome to the audit tables.

After the decision engine produces a :class:`ControlDecision` and the orchestrator
executes it into an :class:`OrchestrationResult`, this writes the full forensic record
— the decision, every action (with clamp flags), guardrail evaluations, any trust
event, and one append-only ``audit_log`` row per state transition — and advances the
experiment's stored ``state``. A promote or revert then evaluates the contract's
graduation rules (§9.4). Shared by the CLI's manual commands and the daemon.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from datatool.control.decision_engine import ControlDecision
from datatool.control.graduation import record_graduation
from datatool.control.orchestrator import OrchestrationResult
from datatool.persistence.repositories import (
    ActionRepository,
    AuditLogRepository,
    DecisionRepository,
    ExperimentRepository,
    GuardrailEvaluationRepository,
    TrustEventRepository,
)


def persist_outcome(
    session: Session,
    experiment_id: uuid.UUID,
    decision: ControlDecision,
    result: OrchestrationResult,
    *,
    adapter_id: str,
    actor: str,
) -> uuid.UUID:
    """Write the decision, actions, guardrail evals, trust event, and transitions.

    Returns the new decision's id. ``adapter_id`` labels actions that did not record
    their own (it is overridden per-action by ``action.adapter``); ``actor`` is the
    audit-log actor (e.g. a username or ``"system"``).
    """
    decision_row = DecisionRepository(session).add(
        experiment_id=experiment_id,
        kind=decision.kind.value,
        reason=decision.reason,
        inputs={},
        outputs=decision.structured_reason,
    )

    guardrail_repo = GuardrailEvaluationRepository(session)
    for evaluation in decision.guardrail_evaluations:
        guardrail_repo.add(
            experiment_id=experiment_id,
            guardrail_name=evaluation.guardrail_name,
            breached=evaluation.breached,
            value=evaluation.value,
            threshold=evaluation.threshold,
            severity=evaluation.severity.value,
            consecutive=evaluation.consecutive_breaches,
        )

    action_repo = ActionRepository(session)
    for action in result.actions:
        action_repo.add(
            experiment_id=experiment_id,
            decision_id=decision_row.id,
            kind=action.kind,
            adapter=action.adapter or adapter_id,
            payload=action.payload,
            clamped=action.clamped,
            clamp_reason=action.clamp_reason,
            succeeded=action.succeeded,
            error=action.error,
        )

    experiment_repo = ExperimentRepository(session)
    audit_repo = AuditLogRepository(session)
    final_state: str | None = None
    for from_state, to_state, reason in result.transitions:
        final_state = to_state.value
        audit_repo.add(
            kind="state.transition",
            actor=actor,
            experiment_id=experiment_id,
            payload={"from": from_state.value, "to": to_state.value, "reason": reason},
        )
    if final_state is not None:
        experiment_repo.set_state(experiment_id, final_state)

    if result.trust_event is not None:
        TrustEventRepository(session).add(
            surface=result.trust_event.surface,
            kind=result.trust_event.kind,
            experiment_id=experiment_id,
            delta=None,
            new_state={"state": final_state} if final_state else {},
            reason=result.trust_event.reason,
        )

    if final_state in ("promoted", "reverted"):
        record_graduation(session, experiment_id, actor=actor, now=datetime.now(UTC))

    return decision_row.id
