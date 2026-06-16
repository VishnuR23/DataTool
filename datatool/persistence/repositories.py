"""Repositories — the persistence API for the control plane (ARCHITECTURE.md §5).

Each repository wraps a SQLAlchemy ``Session`` and exposes the operations the
control loop and adapters need. Append-only tables (``decisions``, ``actions``,
``guardrail_evaluations``, ``trust_events``, ``audit_log``) intentionally expose
**only** insert and query methods — there is no code path here that updates or
deletes a row in them (CLAUDE.md immutable rule). ``experiments`` and
``flag_allocations`` are mutable (they carry ``updated_at``) and may be updated.

Repositories do not commit; the caller owns the transaction (``session_scope``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from datatool.persistence import models as m


class ExperimentRepository:
    """Experiments are mutable: their ``state`` advances over the lifecycle."""

    def __init__(self, session: Session):
        self.session = session

    def add(
        self, *, name: str, surface: str, owner: str, contract: dict, spec: dict, state: str
    ) -> m.Experiment:
        exp = m.Experiment(
            name=name, surface=surface, owner=owner, contract=contract, spec=spec, state=state
        )
        self.session.add(exp)
        self.session.flush()
        return exp

    def get(self, experiment_id: uuid.UUID) -> m.Experiment | None:
        return self.session.get(m.Experiment, experiment_id)

    def get_by_name(self, name: str) -> m.Experiment | None:
        return self.session.scalar(select(m.Experiment).where(m.Experiment.name == name))

    def list(self, *, state: str | None = None, surface: str | None = None) -> list[m.Experiment]:
        stmt = select(m.Experiment)
        if state is not None:
            stmt = stmt.where(m.Experiment.state == state)
        if surface is not None:
            stmt = stmt.where(m.Experiment.surface == surface)
        return list(self.session.scalars(stmt.order_by(m.Experiment.created_at)))

    def set_state(self, experiment_id: uuid.UUID, state: str) -> None:
        exp = self.session.get(m.Experiment, experiment_id)
        if exp is None:
            raise KeyError(f"no experiment {experiment_id}")
        exp.state = state

    def last_revert_at(self, surface: str) -> datetime | None:
        """The most recent time an experiment on this surface entered REVERTED.

        Sourced from the experiment's ``updated_at`` (reverts are also actions, but
        the state column is the simplest authoritative signal for the ledger's
        ``days_since_last_revert``).
        """
        return self.session.scalar(
            select(func.max(m.Experiment.updated_at)).where(
                m.Experiment.surface == surface, m.Experiment.state == "reverted"
            )
        )


class VariantRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(
        self, *, experiment_id: uuid.UUID, name: str, is_control: bool, payload: dict
    ) -> m.Variant:
        variant = m.Variant(
            experiment_id=experiment_id, name=name, is_control=is_control, payload=payload
        )
        self.session.add(variant)
        self.session.flush()
        return variant

    def list_for(self, experiment_id: uuid.UUID) -> list[m.Variant]:
        return list(
            self.session.scalars(select(m.Variant).where(m.Variant.experiment_id == experiment_id))
        )

    def get_by_name(self, experiment_id: uuid.UUID, name: str) -> m.Variant | None:
        return self.session.scalar(
            select(m.Variant).where(
                m.Variant.experiment_id == experiment_id, m.Variant.name == name
            )
        )


class SampleRepository:
    """Samples are an append-only metric history."""

    def __init__(self, session: Session):
        self.session = session

    def add(
        self,
        *,
        experiment_id: uuid.UUID,
        variant_id: uuid.UUID,
        metric: str,
        window_start: datetime,
        window_end: datetime,
        n: int,
        sum: float | None,
        sum_sq: float | None,
        extra: dict | None = None,
    ) -> m.Sample:
        sample = m.Sample(
            experiment_id=experiment_id,
            variant_id=variant_id,
            metric=metric,
            window_start=window_start,
            window_end=window_end,
            n=n,
            sum=sum,
            sum_sq=sum_sq,
            extra=extra,
        )
        self.session.add(sample)
        self.session.flush()
        return sample

    def latest(self, experiment_id: uuid.UUID, metric: str, *, limit: int = 100) -> list[m.Sample]:
        stmt = (
            select(m.Sample)
            .where(m.Sample.experiment_id == experiment_id, m.Sample.metric == metric)
            .order_by(m.Sample.window_end.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))


class DecisionRepository:
    """Append-only: insert and query only."""

    def __init__(self, session: Session):
        self.session = session

    def add(
        self, *, experiment_id: uuid.UUID, kind: str, reason: str, inputs: dict, outputs: dict
    ) -> m.Decision:
        decision = m.Decision(
            experiment_id=experiment_id, kind=kind, reason=reason, inputs=inputs, outputs=outputs
        )
        self.session.add(decision)
        self.session.flush()
        return decision

    def list_for(self, experiment_id: uuid.UUID, *, limit: int = 100) -> list[m.Decision]:
        stmt = (
            select(m.Decision)
            .where(m.Decision.experiment_id == experiment_id)
            .order_by(m.Decision.created_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))


class ActionRepository:
    """Append-only: insert and query only."""

    def __init__(self, session: Session):
        self.session = session

    def add(
        self,
        *,
        experiment_id: uuid.UUID,
        decision_id: uuid.UUID | None,
        kind: str,
        adapter: str,
        payload: dict,
        clamped: bool,
        clamp_reason: str | None,
        succeeded: bool,
        error: str | None,
    ) -> m.Action:
        action = m.Action(
            experiment_id=experiment_id,
            decision_id=decision_id,
            kind=kind,
            adapter=adapter,
            payload=payload,
            clamped=clamped,
            clamp_reason=clamp_reason,
            succeeded=succeeded,
            error=error,
        )
        self.session.add(action)
        self.session.flush()
        return action

    def list_for(self, experiment_id: uuid.UUID, *, limit: int = 100) -> list[m.Action]:
        stmt = (
            select(m.Action)
            .where(m.Action.experiment_id == experiment_id)
            .order_by(m.Action.created_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))


class GuardrailEvaluationRepository:
    """Append-only: insert and query only."""

    def __init__(self, session: Session):
        self.session = session

    def add(
        self,
        *,
        experiment_id: uuid.UUID,
        guardrail_name: str,
        breached: bool,
        value: float | None,
        threshold: float | None,
        severity: str,
        consecutive: int,
    ) -> m.GuardrailEvaluation:
        evaluation = m.GuardrailEvaluation(
            experiment_id=experiment_id,
            guardrail_name=guardrail_name,
            breached=breached,
            value=value,
            threshold=threshold,
            severity=severity,
            consecutive=consecutive,
        )
        self.session.add(evaluation)
        self.session.flush()
        return evaluation

    def list_for(
        self, experiment_id: uuid.UUID, *, limit: int = 100
    ) -> list[m.GuardrailEvaluation]:
        stmt = (
            select(m.GuardrailEvaluation)
            .where(m.GuardrailEvaluation.experiment_id == experiment_id)
            .order_by(m.GuardrailEvaluation.created_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))


class TrustEventRepository:
    """Append-only: insert and query only. Aggregates feed the ledger's DSL."""

    def __init__(self, session: Session):
        self.session = session

    def add(
        self,
        *,
        surface: str,
        kind: str,
        experiment_id: uuid.UUID | None,
        delta: dict | None,
        new_state: dict,
        reason: str,
    ) -> m.TrustEvent:
        event = m.TrustEvent(
            surface=surface,
            kind=kind,
            experiment_id=experiment_id,
            delta=delta,
            new_state=new_state,
            reason=reason,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def list_for_surface(self, surface: str) -> list[m.TrustEvent]:
        return list(
            self.session.scalars(
                select(m.TrustEvent)
                .where(m.TrustEvent.surface == surface)
                .order_by(m.TrustEvent.created_at.desc())
            )
        )

    def count(self, surface: str, kind: str) -> int:
        return (
            self.session.scalar(
                select(func.count())
                .select_from(m.TrustEvent)
                .where(m.TrustEvent.surface == surface, m.TrustEvent.kind == kind)
            )
            or 0
        )


class AuditLogRepository:
    """Append-only firehose: insert and query only."""

    def __init__(self, session: Session):
        self.session = session

    def add(
        self, *, kind: str, actor: str, experiment_id: uuid.UUID | None, payload: dict
    ) -> m.AuditLog:
        entry = m.AuditLog(kind=kind, actor=actor, experiment_id=experiment_id, payload=payload)
        self.session.add(entry)
        self.session.flush()
        return entry

    def list_for(self, experiment_id: uuid.UUID, *, limit: int = 200) -> list[m.AuditLog]:
        stmt = (
            select(m.AuditLog)
            .where(m.AuditLog.experiment_id == experiment_id)
            .order_by(m.AuditLog.created_at)
            .limit(limit)
        )
        return list(self.session.scalars(stmt))


class FlagAllocationRepository:
    """Current allocation per experiment (mutable; upserted)."""

    def __init__(self, session: Session):
        self.session = session

    def get(self, experiment_id: uuid.UUID) -> m.FlagAllocation | None:
        return self.session.get(m.FlagAllocation, experiment_id)

    def upsert(
        self, experiment_id: uuid.UUID, allocations: dict[str, float], *, killed: bool = False
    ) -> m.FlagAllocation:
        row = self.session.get(m.FlagAllocation, experiment_id)
        if row is None:
            row = m.FlagAllocation(
                experiment_id=experiment_id, allocations=allocations, killed=killed
            )
            self.session.add(row)
        else:
            row.allocations = allocations
            row.killed = killed
        self.session.flush()
        return row


class FlagAssignmentRepository:
    """Deterministic unit->variant assignments (stable once assigned)."""

    def __init__(self, session: Session):
        self.session = session

    def get(self, experiment_id: uuid.UUID, unit_id: str) -> m.FlagAssignment | None:
        return self.session.get(m.FlagAssignment, (experiment_id, unit_id))

    def add(
        self, experiment_id: uuid.UUID, unit_id: str, variant_id: uuid.UUID
    ) -> m.FlagAssignment:
        row = m.FlagAssignment(experiment_id=experiment_id, unit_id=unit_id, variant_id=variant_id)
        self.session.add(row)
        self.session.flush()
        return row

    def counts_since(self, experiment_id: uuid.UUID, since: datetime) -> dict[uuid.UUID, int]:
        """Number of assignments per variant id since ``since`` (for SRM)."""
        stmt = (
            select(m.FlagAssignment.variant_id, func.count())
            .where(
                m.FlagAssignment.experiment_id == experiment_id,
                m.FlagAssignment.assigned_at >= since,
            )
            .group_by(m.FlagAssignment.variant_id)
        )
        return {variant_id: count for variant_id, count in self.session.execute(stmt)}
