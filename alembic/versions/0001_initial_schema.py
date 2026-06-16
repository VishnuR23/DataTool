"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-06-15

The full §5 schema for PostgreSQL. (The MVP also supports bootstrapping the same
schema directly with ``datatool init`` / ``create_all``; this migration is the
authoritative, evolvable definition for deployed databases.)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID_PK = sa.text("gen_random_uuid()")
_NOW = sa.text("now()")


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), primary_key=True, server_default=_UUID_PK)


def _created(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=_NOW)


def upgrade() -> None:
    op.create_table(
        "experiments",
        _id(),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("surface", sa.Text(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("contract", postgresql.JSONB(), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        _created(),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
    )
    op.create_index("idx_experiments_state", "experiments", ["state"])
    op.create_index("idx_experiments_surface", "experiments", ["surface"])

    op.create_table(
        "variants",
        _id(),
        sa.Column("experiment_id", sa.Uuid(), sa.ForeignKey("experiments.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_control", sa.Boolean(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        _created(),
        sa.UniqueConstraint("experiment_id", "name", name="uq_variants_experiment_name"),
    )

    op.create_table(
        "samples",
        _id(),
        sa.Column("experiment_id", sa.Uuid(), sa.ForeignKey("experiments.id"), nullable=False),
        sa.Column("variant_id", sa.Uuid(), sa.ForeignKey("variants.id"), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("n", sa.BigInteger(), nullable=False),
        sa.Column("sum", sa.Float(), nullable=True),
        sa.Column("sum_sq", sa.Float(), nullable=True),
        sa.Column("extra", postgresql.JSONB(), nullable=True),
        _created("fetched_at"),
    )
    op.create_index(
        "idx_samples_exp_metric", "samples", ["experiment_id", "metric", sa.text("window_end DESC")]
    )

    op.create_table(
        "decisions",
        _id(),
        sa.Column("experiment_id", sa.Uuid(), sa.ForeignKey("experiments.id"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("outputs", postgresql.JSONB(), nullable=False),
        _created(),
    )
    op.create_index("idx_decisions_exp", "decisions", ["experiment_id", sa.text("created_at DESC")])

    op.create_table(
        "actions",
        _id(),
        sa.Column("experiment_id", sa.Uuid(), sa.ForeignKey("experiments.id"), nullable=False),
        sa.Column("decision_id", sa.Uuid(), sa.ForeignKey("decisions.id"), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("adapter", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("clamped", sa.Boolean(), nullable=False),
        sa.Column("clamp_reason", sa.Text(), nullable=True),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        _created(),
    )
    op.create_index("idx_actions_exp", "actions", ["experiment_id", sa.text("created_at DESC")])

    op.create_table(
        "guardrail_evaluations",
        _id(),
        sa.Column("experiment_id", sa.Uuid(), sa.ForeignKey("experiments.id"), nullable=False),
        sa.Column("guardrail_name", sa.Text(), nullable=False),
        sa.Column("breached", sa.Boolean(), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("threshold", sa.Float(), nullable=True),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("consecutive", sa.Integer(), nullable=False),
        _created(),
    )
    op.create_index(
        "idx_guardrails_exp",
        "guardrail_evaluations",
        ["experiment_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "trust_events",
        _id(),
        sa.Column("surface", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), sa.ForeignKey("experiments.id"), nullable=True),
        sa.Column("delta", postgresql.JSONB(), nullable=True),
        sa.Column("new_state", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        _created(),
    )
    op.create_index("idx_trust_surface", "trust_events", ["surface", sa.text("created_at DESC")])

    op.create_table(
        "audit_log",
        _id(),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), sa.ForeignKey("experiments.id"), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        _created(),
    )
    op.create_index("idx_audit_kind", "audit_log", ["kind", sa.text("created_at DESC")])

    op.create_table(
        "flag_assignments",
        sa.Column("experiment_id", sa.Uuid(), sa.ForeignKey("experiments.id"), primary_key=True),
        sa.Column("unit_id", sa.Text(), primary_key=True),
        sa.Column("variant_id", sa.Uuid(), sa.ForeignKey("variants.id"), nullable=False),
        _created("assigned_at"),
    )

    op.create_table(
        "flag_allocations",
        sa.Column("experiment_id", sa.Uuid(), sa.ForeignKey("experiments.id"), primary_key=True),
        sa.Column("allocations", postgresql.JSONB(), nullable=False),
        sa.Column("killed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
    )

    op.create_table(
        "llm_variant_cache",
        sa.Column("cache_key", sa.Text(), primary_key=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        _created(),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    for table in (
        "llm_variant_cache",
        "flag_allocations",
        "flag_assignments",
        "audit_log",
        "trust_events",
        "guardrail_evaluations",
        "actions",
        "decisions",
        "samples",
        "variants",
        "experiments",
    ):
        op.drop_table(table)
