"""Evaluate graduation rules after a terminal action (ARCHITECTURE.md §9.4).

:mod:`datatool.control.ledger` holds the pure rule engine; this module is its
persistence side. It rebuilds a surface's :class:`SurfaceTrustState` from the audit
tables, asks the ledger which rules fire, and records each fired rule as a
``TrustEvent`` carrying the signed delta (plus an ``audit_log`` row). The written
contract is never touched: :func:`load_effective_contract` layers the deltas on at
resolution time and ``apply_field_deltas`` clamps the result to a valid contract.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from datatool.control.ledger import GraduationOutcome, SurfaceTrustState, evaluate_graduation
from datatool.control.loader import load_effective_contract
from datatool.core.models import GraduationRule
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


def rule_key(rule: GraduationRule) -> str:
    """A stable fingerprint of a rule's content (its `when` and `action`)."""
    canonical = json.dumps({"when": rule.when, "action": rule.action}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _rule_cooldowns(events: list[m.TrustEvent], rules: list[GraduationRule]) -> dict[int, datetime]:
    """Each current rule's newest recorded cooldown, mapped onto its index in ``rules``.

    Cooldowns are keyed by rule *content*, so experiments on one surface that list
    the same rule in different positions share its cooldown, and different rules
    never collide. Events written before keys existed fall back to their index.
    """
    by_key: dict[str, datetime] = {}
    by_index: dict[int, datetime] = {}
    for event in events:  # newest first: keep the first cooldown seen per rule
        state = event.new_state or {}
        until = state.get("cooldown_until")
        if event.kind not in _RULE_EVENT_KINDS or not until:
            continue
        when = _utc(datetime.fromisoformat(until))
        if (key := state.get("rule_key")) is not None:
            by_key.setdefault(key, when)
        elif (index := state.get("rule_index")) is not None:
            by_index.setdefault(index, when)

    cooldowns: dict[int, datetime] = {}
    for index, rule in enumerate(rules):
        until = by_key.get(rule_key(rule), by_index.get(index))
        if until is not None:
            cooldowns[index] = until
    return cooldowns


def record_graduation(
    session: Session, experiment_id: uuid.UUID, *, actor: str, now: datetime
) -> list[GraduationOutcome]:
    """Evaluate the experiment's graduation rules for its surface; record what fires.

    # DECISION: the rules come from the experiment that just reached a terminal
    # state; cooldowns are surface-wide and keyed by rule content (see rule_key).
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
        rule_cooldowns=_rule_cooldowns(trust.list_for_surface(surface), contract.graduation.rules),
    )
    outcomes = evaluate_graduation(contract.graduation, state, now=now)

    audit = AuditLogRepository(session)
    for outcome in outcomes:
        key = rule_key(contract.graduation.rules[outcome.rule_index])
        cooldown = outcome.cooldown_until.isoformat() if outcome.cooldown_until else None
        trust.add(
            surface=surface,
            kind=outcome.kind,
            experiment_id=experiment_id,
            delta={outcome.field: outcome.delta},
            new_state={
                "rule_key": key,
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
                "rule_key": key,
                "rule_index": outcome.rule_index,
                "field": outcome.field,
                "delta": outcome.delta,
                "new_value": outcome.new_value,
                "cooldown_until": cooldown,
            },
        )
    return outcomes
