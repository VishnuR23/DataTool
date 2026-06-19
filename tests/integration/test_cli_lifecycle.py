"""Manual lifecycle commands: pause, resume, revert, promote (§12).

Experiments are registered (proposed) then nudged into a running state via the
repositories — the daemon that would do this autonomously is step 6 — so the manual
commands can be exercised against realistic state.
"""

from __future__ import annotations

from datatool.core.models import State
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import (
    ExperimentRepository,
    FlagAllocationRepository,
    TrustEventRepository,
)


def _register_running(cli_env, *, state: str, treatment: float = 5.0):
    """Register the example experiment and move it into a running state."""
    assert cli_env.invoke("register", cli_env.example()).exit_code == 0
    with session_scope(cli_env.factory()) as s:
        exp = ExperimentRepository(s).get_by_name("pricing-headline-clarity")
        ExperimentRepository(s).set_state(exp.id, state)
        FlagAllocationRepository(s).upsert(
            exp.id, {"control": 100.0 - treatment, "treatment": treatment}
        )
        return exp.id


def _state(cli_env, eid) -> str:
    with session_scope(cli_env.factory()) as s:
        return ExperimentRepository(s).get(eid).state


def test_pause_moves_a_running_experiment_to_holding(cli_env):
    eid = _register_running(cli_env, state="ramping")
    result = cli_env.invoke("pause", "pricing-headline-clarity")
    assert result.exit_code == 0
    assert _state(cli_env, eid) == State.HOLDING.value


def test_resume_moves_a_held_experiment_back_to_ramping(cli_env):
    eid = _register_running(cli_env, state="holding")
    assert cli_env.invoke("resume", "pricing-headline-clarity").exit_code == 0
    assert _state(cli_env, eid) == State.RAMPING.value


def test_pause_on_proposed_experiment_is_illegal(cli_env):
    cli_env.invoke("register", cli_env.example())  # stays proposed
    result = cli_env.invoke("pause", "pricing-headline-clarity")
    assert result.exit_code == 1  # proposed -> holding is not a legal transition


def test_revert_kills_and_transitions_to_reverted(cli_env):
    eid = _register_running(cli_env, state="ramping", treatment=5.0)
    result = cli_env.invoke("revert", "pricing-headline-clarity", "--reason", "regression")
    assert result.exit_code == 0
    assert _state(cli_env, eid) == State.REVERTED.value
    with session_scope(cli_env.factory()) as s:
        allocation = FlagAllocationRepository(s).get(eid)
        assert allocation.killed is True


def test_promote_from_holding_ships_and_records_clean_promotion(cli_env):
    eid = _register_running(cli_env, state="holding", treatment=5.0)
    result = cli_env.invoke("promote", "pricing-headline-clarity")
    assert result.exit_code == 0
    assert _state(cli_env, eid) == State.PROMOTED.value
    with session_scope(cli_env.factory()) as s:
        assert FlagAllocationRepository(s).get(eid).allocations == {
            "control": 0.0,
            "treatment": 100.0,
        }
        assert TrustEventRepository(s).count("pricing-page", "clean_promotion") == 1


def test_promote_off_holding_requires_force(cli_env):
    _register_running(cli_env, state="ramping")
    blocked = cli_env.invoke("promote", "pricing-headline-clarity")
    assert blocked.exit_code == 1
    assert "--force" in blocked.output
    forced = cli_env.invoke("promote", "pricing-headline-clarity", "--force")
    assert forced.exit_code == 0


def test_revert_after_promotion_records_false_positive_ship(cli_env):
    _register_running(cli_env, state="holding", treatment=5.0)
    assert cli_env.invoke("promote", "pricing-headline-clarity").exit_code == 0
    assert cli_env.invoke("revert", "pricing-headline-clarity").exit_code == 0
    with session_scope(cli_env.factory()) as s:
        assert TrustEventRepository(s).count("pricing-page", "false_positive_ship") == 1
