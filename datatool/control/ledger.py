"""Trust ledger — autonomy graduation (ARCHITECTURE.md §9.4).

After a terminal action (promote or revert) the ledger asks: given this surface's
track record, should the controller earn *more* autonomy or have it *taken away*?
Graduation rules are (condition, action) pairs. A condition is a small DSL over the
surface's trust history; an action increases or decreases a contract field
(typically ``max_autonomous_pct``), clamped by ``up_to`` / ``down_to`` and rate-
limited by a ``cooldown``.

Crucially, the ledger never rewrites contract files. A fired rule produces a
*delta* (recorded as a ``TrustEvent``); the effective contract is the written
contract plus the accumulated deltas, computed at resolution time
(:func:`apply_field_deltas`). Contracts on disk stay stable and reviewable; trust
is layered on top.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import TypeAdapter

from datatool.core.exceptions import ContractError
from datatool.core.models import Graduation, TrustContract

# Comparison operators usable in a `when` clause (ARCHITECTURE.md §9.4).
_OPERATORS = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    ">": operator.gt,
    "<": operator.lt,
}

# Trust deltas are only supported for this contract field in the MVP; it is the
# one the graduation examples adjust. Others raise rather than silently no-op.
_SUPPORTED_DELTA_FIELDS = frozenset({"max_autonomous_pct"})

_timedelta_adapter = TypeAdapter(timedelta)


@dataclass
class SurfaceTrustState:
    """A surface's trust history, as the graduation DSL sees it."""

    surface: str
    clean_promotions_on_surface: int
    false_positive_ships: int
    # None means the surface has never been reverted -> treated as +infinity days.
    days_since_last_revert: float | None
    # Current *effective* contract field values, used to apply up_to/down_to.
    current_field_values: dict[str, float]
    # rule index -> the time it may next fire (cooldown enforcement).
    rule_cooldowns: dict[int, datetime] | None = None


@dataclass
class GraduationOutcome:
    """A fired graduation rule's effect: a signed delta to a contract field."""

    rule_index: int
    field: str
    delta: float  # signed change (e.g. +5 to graduate, -10 to demote)
    new_value: float
    kind: str  # 'graduate' (delta > 0) | 'demote' (delta < 0)
    reason: str
    cooldown_until: datetime | None


def _metric_value(name: str, state: SurfaceTrustState) -> float:
    if name == "clean_promotions_on_surface":
        return float(state.clean_promotions_on_surface)
    if name == "false_positive_ships":
        return float(state.false_positive_ships)
    if name == "days_since_last_revert":
        # Never reverted reads as "infinitely stable", so '>= N' is satisfied.
        return (
            float("inf") if state.days_since_last_revert is None else state.days_since_last_revert
        )
    raise ContractError(f"unknown graduation condition metric: {name!r}")


def _parse_condition(raw: object) -> tuple[str, float]:
    """Parse a `when` value into (operator, number). A bare number means '=='."""
    if isinstance(raw, bool):  # guard: bool is an int subclass
        raise ContractError(f"graduation condition must be numeric, got bool {raw!r}")
    if isinstance(raw, (int, float)):
        return "==", float(raw)
    text = str(raw).strip()
    for op in (">=", "<=", "==", ">", "<"):  # longest operators first
        if text.startswith(op):
            return op, float(text[len(op) :].strip())
    return "==", float(text)  # bare number as a string


def _condition_holds(raw: object, metric: str, state: SurfaceTrustState) -> bool:
    op, threshold = _parse_condition(raw)
    return _OPERATORS[op](_metric_value(metric, state), threshold)


def _parse_cooldown(raw: object) -> timedelta | None:
    if raw is None:
        return None
    if isinstance(raw, timedelta):
        return raw
    return _timedelta_adapter.validate_python(raw)  # ISO-8601 duration like 'P30D'


def evaluate_graduation(
    graduation: Graduation,
    state: SurfaceTrustState,
    *,
    now: datetime,
) -> list[GraduationOutcome]:
    """Return the graduation outcomes that fire for this surface right now.

    A rule fires when *all* of its `when` conditions hold and it is not within its
    cooldown. Its action is applied to the current effective field value, clamped
    by ``up_to``/``down_to`` and by the absolute percentage bounds; a rule that
    would produce no net change is skipped.
    """
    cooldowns = state.rule_cooldowns or {}
    outcomes: list[GraduationOutcome] = []

    for index, rule in enumerate(graduation.rules):
        cooldown_until = cooldowns.get(index)
        if cooldown_until is not None and now < cooldown_until:
            continue  # still cooling down; cannot re-fire

        if not all(_condition_holds(v, metric, state) for metric, v in rule.when.items()):
            continue

        outcome = _apply_action(index, rule.action, state, now)
        if outcome is not None:
            outcomes.append(outcome)

    return outcomes


def _apply_action(
    index: int, action: dict, state: SurfaceTrustState, now: datetime
) -> GraduationOutcome | None:
    field = action.get("field")
    if field not in _SUPPORTED_DELTA_FIELDS:
        raise ContractError(
            f"graduation action targets unsupported field {field!r}; "
            f"supported: {sorted(_SUPPORTED_DELTA_FIELDS)}"
        )
    if field not in state.current_field_values:
        raise ContractError(f"no current value known for field {field!r} on this surface")

    op = action.get("op")
    by = float(action.get("by", 0.0))
    current = state.current_field_values[field]

    if op == "increase":
        new_value = current + by
    elif op == "decrease":
        new_value = current - by
    else:
        raise ContractError(f"graduation action op must be 'increase'/'decrease', got {op!r}")

    if "up_to" in action:
        new_value = min(new_value, float(action["up_to"]))
    if "down_to" in action:
        new_value = max(new_value, float(action["down_to"]))
    new_value = max(0.0, min(100.0, new_value))  # absolute percentage bounds

    delta = new_value - current
    if delta == 0:
        return None  # already at the clamp; nothing to record

    cooldown = _parse_cooldown(action.get("cooldown"))
    return GraduationOutcome(
        rule_index=index,
        field=field,
        delta=delta,
        new_value=new_value,
        kind="graduate" if delta > 0 else "demote",
        reason=(
            f"{op} {field} by {by}"
            + (f" up_to {action['up_to']}" if "up_to" in action else "")
            + (f" down_to {action['down_to']}" if "down_to" in action else "")
        ),
        cooldown_until=(now + cooldown) if cooldown is not None else None,
    )


def apply_field_deltas(contract: TrustContract, deltas: dict[str, float]) -> TrustContract:
    """Return the effective contract with accumulated trust deltas applied.

    Only ``max_autonomous_pct`` is adjustable in the MVP. The result is clamped to
    a valid range — never below the initial canary (which must stay <= the ceiling)
    and never above 100 — so the effective contract is always itself valid.
    """
    allocation = contract.allocation
    for field, delta in deltas.items():
        if field != "max_autonomous_pct":
            raise ContractError(f"trust delta for unsupported field {field!r}")
        new_ceiling = allocation.max_autonomous_pct + delta
        new_ceiling = max(allocation.initial_canary_pct, min(100.0, new_ceiling))
        allocation = allocation.model_copy(update={"max_autonomous_pct": new_ceiling})
    return contract.model_copy(update={"allocation": allocation})
