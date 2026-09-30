"""Evaluate graduation rules after a terminal action (ARCHITECTURE.md §9.4).

:mod:`datatool.control.ledger` holds the pure rule engine; this module is its
persistence side. It rebuilds a surface's :class:`SurfaceTrustState` from the audit
tables, asks the ledger which rules fire, and records each fired rule as a
``TrustEvent`` carrying the signed delta (plus an ``audit_log`` row). The written
contract is never touched: :func:`load_effective_contract` layers the deltas on at
resolution time and ``apply_field_deltas`` clamps the result to a valid contract.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from datatool.control.ledger import GraduationOutcome, SurfaceTrustState, evaluate_graduation
from datatool.control.loader import load_effective_contract
from datatool.persistence import models as m
from datatool.persistence.repositories import (
    AuditLogRepository,
    ExperimentRepository,
    TrustEventRepository,
)

_RULE_EVENT_KINDS = ("graduate", "demote")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _days_since_last_revert(session: Session, surface: str, now: datetime) -> float | None:
    last_kill = session.scalar(
        select(func.max(m.Action.created_at))
        .join(m.Experiment, m.Action.experiment_id == m.Experiment.id)
        .where(m.Experiment.surface == surface, m.Action.kind == "kill", m.Action.succeeded)
    )
    if last_kill is None:
        return None
    return (now - _utc(last_kill)).total_seconds() / 86400


def _rule_cooldowns(events: list[m.TrustEvent]) -> dict[int, datetime]:
    """The newest recorded cooldown per rule index (events arrive newest first)."""
    cooldowns: dict[int, datetime] = {}
    for event in events:
        state = event.new_state or {}
        index, until = state.get("rule_index"), state.get("cooldown_until")
        if (
            event.kind in _RULE_EVENT_KINDS
            and index is not None
            and until
            and index not in cooldowns
        ):
            cooldowns[index] = _utc(datetime.fromisoformat(until))
    return cooldowns


def record_graduation(
    session: Session, experiment_id: uuid.UUID, *, actor: str, now: datetime
) -> list[GraduationOutcome]:
    """Evaluate the experiment's graduation rules for its surface; record what fires.

    # DECISION: the rules come from the experiment that just reached a terminal
    # state, and cooldowns are keyed by rule index on the surface. Experiments on one
    # surface normally share its contract layer (config/surfaces/), so the indices
    # line up; differing per-experiment rule lists would share cooldown slots.
    """
    experiment = ExperimentRepository(session).get(experiment_id)
    contract = load_effective_contract(session, experiment)
    if not contract.graduation.rules:
        return []

    surface = experiment.surface
    trust = TrustEventRepository(session)
    state = SurfaceTrustState(
        surface=surface,
        clean_promotions_on_surface=trust.count(surface, "clean_promotion"),
        false_positive_ships=trust.count(surface, "false_positive_ship"),
        days_since_last_revert=_days_since_last_revert(session, surface, now),
        current_field_values={"max_autonomous_pct": contract.allocation.max_autonomous_pct},
        rule_cooldowns=_rule_cooldowns(trust.list_for_surface(surface)),
    )
    outcomes = evaluate_graduation(contract.graduation, state, now=now)

    audit = AuditLogRepository(session)
    for outcome in outcomes:
        cooldown = outcome.cooldown_until.isoformat() if outcome.cooldown_until else None
        trust.add(
            surface=surface,
            kind=outcome.kind,
            experiment_id=experiment_id,
            delta={outcome.field: outcome.delta},
            new_state={
                "rule_index": outcome.rule_index,
                "new_value": outcome.new_value,
                "cooldown_until": cooldown,
            },
            reason=f"graduation rule {outcome.rule_index}: {outcome.reason}",
        )
        audit.add(
            kind="trust.graduated" if outcome.kind == "graduate" else "trust.demoted",
            actor=actor,
            experiment_id=experiment_id,
            payload={
                "surface": surface,
                "rule_index": outcome.rule_index,
                "field": outcome.field,
                "delta": outcome.delta,
                "new_value": outcome.new_value,
                "cooldown_until": cooldown,
            },
        )
    return outcomes
