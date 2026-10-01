"""Reconstruct control-plane objects from persisted state.

The control loop and the CLI both need to turn a stored ``experiments`` row back into
the in-memory objects the decision engine and orchestrator operate on: the *effective*
trust contract (the written contract plus accumulated trust-ledger deltas) and a live
:class:`ExperimentRuntime`. This module is the bridge, shared by the CLI's manual
lifecycle commands and (later) the daemon's tick loop.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from datatool.control.ledger import apply_field_deltas
from datatool.control.runtime import ExperimentRuntime
from datatool.core.models import State, TrustContract
from datatool.core.state_machine import TERMINAL_STATES
from datatool.persistence import models as m
from datatool.persistence.repositories import (
    DecisionRepository,
    FlagAllocationRepository,
    GuardrailEvaluationRepository,
    TrustEventRepository,
)
from datatool.stats.fdr import LORDController


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def net_trust_deltas(session: Session, surface: str) -> dict[str, float]:
    """Sum the trust-ledger deltas recorded for a surface, per contract field."""
    totals: dict[str, float] = {}
    for event in TrustEventRepository(session).list_for_surface(surface):
        if not event.delta:
            continue
        for field, delta in event.delta.items():
            totals[field] = totals.get(field, 0.0) + float(delta)
    return totals


def load_effective_contract(session: Session, experiment: m.Experiment) -> TrustContract:
    """The written contract with accumulated trust deltas applied (§9.4).

    Contracts on disk/DB are stable; the effective contract is computed by layering
    the surface's trust deltas onto the stored contract at read time.
    """
    base = TrustContract.model_validate(experiment.contract)
    deltas = net_trust_deltas(session, experiment.surface)
    return apply_field_deltas(base, deltas) if deltas else base


def load_runtime(session: Session, experiment: m.Experiment) -> ExperimentRuntime:
    """Reconstruct the live :class:`ExperimentRuntime` for an experiment."""
    allocation = FlagAllocationRepository(session).get(experiment.id)
    treatment_pct = 0.0
    if allocation is not None and not allocation.killed:
        treatment_pct = float(allocation.allocations.get("treatment", 0.0))

    # Most recent consecutive-breach count per guardrail (list is newest-first).
    breach_counts: dict[str, int] = {}
    for evaluation in GuardrailEvaluationRepository(session).list_for(experiment.id):
        breach_counts.setdefault(evaluation.guardrail_name, evaluation.consecutive)

    has_promoted = experiment.state == State.PROMOTED.value or any(
        event.kind == "clean_promotion" and event.experiment_id == experiment.id
        for event in TrustEventRepository(session).list_for_surface(experiment.surface)
    )

    recent_decision = DecisionRepository(session).list_for(experiment.id, limit=1)
    last_decision_at = _to_utc(recent_decision[0].created_at) if recent_decision else None

    return ExperimentRuntime(
        experiment_id=experiment.id,
        name=experiment.name,
        surface=experiment.surface,
        state=State(experiment.state),
        current_allocation_pct=treatment_pct,
        started_at=_to_utc(experiment.created_at),
        last_decision_at=last_decision_at,
        guardrail_breach_counts=breach_counts,
        has_promoted=has_promoted,
    )


_TERMINAL_VALUES = frozenset(state.value for state in TERMINAL_STATES)


def terminal_outcomes(session: Session) -> list[bool]:
    """Every concluded experiment as one LORD test, in order: ``True`` = discovery.

    Read from the append-only ``state.transition`` audit rows, which every path
    writes (daemon and manual CLI alike). An experiment counts once, at its first
    terminal transition; a promotion is a discovery, a revert or conclusion is not.
    A later revert of a promoted experiment is a trust event, not a second test.
    """
    rows = session.scalars(
        select(m.AuditLog)
        .where(m.AuditLog.kind == "state.transition")
        .order_by(m.AuditLog.created_at)
    )
    seen: set = set()
    outcomes: list[bool] = []
    for row in rows:
        to = (row.payload or {}).get("to")
        if to in _TERMINAL_VALUES and row.experiment_id not in seen:
            seen.add(row.experiment_id)
            outcomes.append(to == State.PROMOTED.value)
    return outcomes


def sync_lord_from_history(session: Session, lord: LORDController) -> None:
    """Reset ``lord`` to exactly the persisted test history (Javanmard & Montanari 2018).

    LORD's FDR guarantee assumes it sees every test once, in order; its full state
    is that ordered outcome list. Holding it only in memory meant a daemon restart
    re-spent the initial wealth ``w0`` and manual promotes/reverts were never
    counted. Replaying from the audit log makes the persisted history authoritative.
    """
    lord.tests_seen = 0
    lord.rejections = []
    for rejected in terminal_outcomes(session):
        lord.record_outcome(rejected)
