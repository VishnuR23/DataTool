"""SQLAlchemy ORM models for the §5 schema.

One model per table in ARCHITECTURE.md §5. Audit-relevant tables
(``decisions``, ``actions``, ``guardrail_evaluations``, ``trust_events``,
``audit_log``) are append-only by contract (CLAUDE.md): the repositories expose no
update or delete for them, and nothing here implies mutation. UUID primary keys
default in Python (portable across Postgres and the SQLite test database);
timestamps default to ``now()`` in UTC.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from datatool.persistence.db import JSONB, Base


def _new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=_new_uuid)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    surface: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    owner: Mapped[str] = mapped_column(Text, nullable=False)
    contract: Mapped[dict] = mapped_column(JSONB, nullable=False)  # full TrustContract
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)  # variants, goal, scope, ...
    state: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class Variant(Base):
    __tablename__ = "variants"
    __table_args__ = (
        UniqueConstraint("experiment_id", "name", name="uq_variants_experiment_name"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("experiments.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_control: Mapped[bool] = mapped_column(Boolean, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)  # adapter-specific ref
    created_at: Mapped[datetime] = _created_at()


class Sample(Base):
    __tablename__ = "samples"

    id: Mapped[uuid.UUID] = _uuid_pk()
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("experiments.id"), nullable=False, index=True
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("variants.id"), nullable=False)
    metric: Mapped[str] = mapped_column(Text, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    n: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sum: Mapped[float | None] = mapped_column(Float, nullable=True)
    sum_sq: Mapped[float | None] = mapped_column(Float, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    fetched_at: Mapped[datetime] = _created_at()


class Decision(Base):
    """Append-only (CLAUDE.md): the decision engine's output, one row per cycle."""

    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("experiments.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    inputs: Mapped[dict] = mapped_column(JSONB, nullable=False)  # snapshot of stats inputs
    outputs: Mapped[dict] = mapped_column(JSONB, nullable=False)  # CS bounds, posterior, ...
    created_at: Mapped[datetime] = _created_at()


class Action(Base):
    """Append-only: every action the orchestrator takes on the execution plane."""

    __tablename__ = "actions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("experiments.id"), nullable=False, index=True
    )
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("decisions.id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # allocate|kill|promote|notify
    adapter: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    clamped: Mapped[bool] = mapped_column(Boolean, nullable=False)
    clamp_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()


class GuardrailEvaluation(Base):
    """Append-only: every guardrail check."""

    __tablename__ = "guardrail_evaluations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("experiments.id"), nullable=False, index=True
    )
    guardrail_name: Mapped[str] = mapped_column(Text, nullable=False)
    breached: Mapped[bool] = mapped_column(Boolean, nullable=False)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    consecutive: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class TrustEvent(Base):
    """Append-only: graduation/demotion events on the trust ledger."""

    __tablename__ = "trust_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    surface: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("experiments.id"), nullable=True
    )
    delta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    new_state: Mapped[dict] = mapped_column(JSONB, nullable=False)  # snapshot of ledger state
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class AuditLog(Base):
    """Append-only firehose for everything worth recording."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = _uuid_pk()
    kind: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    actor: Mapped[str] = mapped_column(Text, nullable=False)  # user, 'system', adapter id
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("experiments.id"), nullable=True
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class FlagAssignment(Base):
    """Reference flag adapter: which variant each unit is assigned to."""

    __tablename__ = "flag_assignments"

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("experiments.id"), primary_key=True
    )
    unit_id: Mapped[str] = mapped_column(Text, primary_key=True)
    variant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("variants.id"), nullable=False)
    assigned_at: Mapped[datetime] = _created_at()


class FlagAllocation(Base):
    """Reference flag adapter: current allocation per experiment."""

    __tablename__ = "flag_allocations"

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("experiments.id"), primary_key=True
    )
    allocations: Mapped[dict] = mapped_column(JSONB, nullable=False)  # {"control": 99, ...}
    killed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class LLMVariantCache(Base):
    """Deterministic cache of LLM variant generations (used by the LLM adapter)."""

    __tablename__ = "llm_variant_cache"

    cache_key: Mapped[str] = mapped_column(Text, primary_key=True)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created_at()
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
