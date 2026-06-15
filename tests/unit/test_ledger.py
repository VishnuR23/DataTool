"""Trust ledger graduation logic (ARCHITECTURE.md §9.4).

These pin the autonomy graduation DSL against the canonical rules from §7: a clean
track record earns more autonomy (graduate), a false-positive ship takes it away
(demote), clamps and cooldowns are respected, and trust deltas applied to a
contract stay within valid bounds.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from datatool.control.ledger import (
    GraduationOutcome,
    SurfaceTrustState,
    apply_field_deltas,
    evaluate_graduation,
)
from datatool.core.exceptions import ContractError
from datatool.core.models import Allocation, Graduation, GraduationRule

from .conftest import make_contract

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

# The canonical §7 graduation rules.
GRADUATE_RULE = GraduationRule(
    when={"clean_promotions_on_surface": ">=3", "false_positive_ships": 0},
    action={"field": "max_autonomous_pct", "op": "increase", "by": 5, "up_to": 25},
)
DEMOTE_RULE = GraduationRule(
    when={"false_positive_ships": ">=1"},
    action={"field": "max_autonomous_pct", "op": "decrease", "by": 10, "cooldown": "P30D"},
)
GRADUATION = Graduation(rules=[GRADUATE_RULE, DEMOTE_RULE])


def _state(*, clean=0, fps=0, days=None, current=5.0, cooldowns=None):
    return SurfaceTrustState(
        surface="pricing-page",
        clean_promotions_on_surface=clean,
        false_positive_ships=fps,
        days_since_last_revert=days,
        current_field_values={"max_autonomous_pct": current},
        rule_cooldowns=cooldowns,
    )


def _only(outcomes: list[GraduationOutcome]) -> GraduationOutcome:
    assert len(outcomes) == 1
    return outcomes[0]


# --------------------------------------------------------------------------- #
# Graduate
# --------------------------------------------------------------------------- #


def test_clean_record_graduates_autonomy():
    """3 clean promotions and 0 false-positive ships graduates +5 (up to 25)."""
    out = _only(evaluate_graduation(GRADUATION, _state(clean=3, fps=0, current=5.0), now=NOW))
    assert out.kind == "graduate"
    assert out.delta == 5.0
    assert out.new_value == 10.0
    assert out.cooldown_until is None


def test_graduation_respects_up_to_clamp():
    """Near the cap, the increase is clamped by up_to (22 + 5 -> 25, not 27)."""
    out = _only(evaluate_graduation(GRADUATION, _state(clean=5, fps=0, current=22.0), now=NOW))
    assert out.new_value == 25.0
    assert out.delta == 3.0


def test_graduation_at_cap_produces_no_outcome():
    """Already at the cap, a graduate rule yields no delta and is not recorded."""
    outcomes = evaluate_graduation(GRADUATION, _state(clean=5, fps=0, current=25.0), now=NOW)
    assert outcomes == []


def test_graduate_rule_does_not_fire_with_a_false_positive_ship():
    """The graduate rule requires false_positive_ships == 0; one ship blocks it."""
    outcomes = evaluate_graduation(GRADUATION, _state(clean=5, fps=1, current=5.0), now=NOW)
    # Only the demote rule fires here, not the graduate rule.
    assert all(o.kind == "demote" for o in outcomes)


# --------------------------------------------------------------------------- #
# Demote
# --------------------------------------------------------------------------- #


def test_false_positive_ship_demotes_autonomy_with_cooldown():
    """A false-positive ship decreases the ceiling by 10 and sets a 30-day cooldown."""
    out = _only(evaluate_graduation(GRADUATION, _state(clean=0, fps=1, current=15.0), now=NOW))
    assert out.kind == "demote"
    assert out.delta == -10.0
    assert out.new_value == 5.0
    assert out.cooldown_until == NOW + timedelta(days=30)


def test_demote_rule_in_cooldown_does_not_refire():
    """A rule within its cooldown window is skipped."""
    cooldowns = {1: NOW + timedelta(days=10)}  # demote rule (index 1) cooling down
    outcomes = evaluate_graduation(
        GRADUATION, _state(fps=1, current=15.0, cooldowns=cooldowns), now=NOW
    )
    assert outcomes == []


def test_demote_rule_refires_after_cooldown_expires():
    """Once the cooldown has passed, the rule can fire again."""
    cooldowns = {1: NOW - timedelta(days=1)}  # expired yesterday
    out = _only(
        evaluate_graduation(GRADUATION, _state(fps=1, current=15.0, cooldowns=cooldowns), now=NOW)
    )
    assert out.kind == "demote"


# --------------------------------------------------------------------------- #
# Condition DSL
# --------------------------------------------------------------------------- #


def test_days_since_last_revert_treats_never_reverted_as_infinite():
    """A 'days_since_last_revert >= 30' rule fires when the surface was never reverted."""
    g = Graduation(
        rules=[
            GraduationRule(
                when={"days_since_last_revert": ">=30"},
                action={"field": "max_autonomous_pct", "op": "increase", "by": 5, "up_to": 50},
            )
        ]
    )
    fired = evaluate_graduation(g, _state(days=None, current=5.0), now=NOW)
    assert _only(fired).kind == "graduate"
    # But with only 10 days since the last revert, it does not fire.
    assert evaluate_graduation(g, _state(days=10.0, current=5.0), now=NOW) == []


def test_unknown_condition_metric_raises():
    """An unrecognized condition metric raises rather than silently passing."""
    g = Graduation(
        rules=[
            GraduationRule(
                when={"made_up_metric": ">=1"},
                action={"field": "max_autonomous_pct", "op": "increase", "by": 1},
            )
        ]
    )
    with pytest.raises(ContractError):
        evaluate_graduation(g, _state(clean=5), now=NOW)


def test_unsupported_action_field_raises():
    """A graduation action on an unsupported field raises."""
    g = Graduation(
        rules=[
            GraduationRule(
                when={"clean_promotions_on_surface": ">=1"},
                action={"field": "alpha", "op": "increase", "by": 1},
            )
        ]
    )
    with pytest.raises(ContractError):
        evaluate_graduation(g, _state(clean=5), now=NOW)


# --------------------------------------------------------------------------- #
# apply_field_deltas (effective contract)
# --------------------------------------------------------------------------- #


def test_apply_positive_delta_raises_effective_ceiling():
    contract = make_contract(allocation=Allocation(max_autonomous_pct=5.0, initial_canary_pct=1.0))
    effective = apply_field_deltas(contract, {"max_autonomous_pct": 5.0})
    assert effective.allocation.max_autonomous_pct == 10.0
    # The written contract is unchanged (deltas are layered, not persisted).
    assert contract.allocation.max_autonomous_pct == 5.0


def test_apply_negative_delta_clamps_to_initial_canary():
    contract = make_contract(allocation=Allocation(max_autonomous_pct=5.0, initial_canary_pct=1.0))
    effective = apply_field_deltas(contract, {"max_autonomous_pct": -10.0})
    assert effective.allocation.max_autonomous_pct == 1.0  # not below the canary


def test_apply_large_positive_delta_clamps_to_hundred():
    contract = make_contract(allocation=Allocation(max_autonomous_pct=5.0))
    effective = apply_field_deltas(contract, {"max_autonomous_pct": 500.0})
    assert effective.allocation.max_autonomous_pct == 100.0


def test_apply_delta_to_unsupported_field_raises():
    with pytest.raises(ContractError):
        apply_field_deltas(make_contract(), {"holdout_pct": 1.0})
