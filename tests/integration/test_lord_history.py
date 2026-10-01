"""LORD's state is rebuilt from the audit log, not held only in memory (§8.2).

The online-FDR guarantee depends on the controller seeing every test, in order,
exactly once. Its whole state is that ordered outcome list, so it is replayed from
the append-only audit log: one test per experiment, at its first terminal
transition, a promotion being a discovery. A restart must not re-spend the initial
wealth, and manual promotes/reverts (another process) must count.
"""

from __future__ import annotations

from datatool.control.loader import sync_lord_from_history, terminal_outcomes
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import AuditLogRepository, ExperimentRepository
from datatool.stats.fdr import LORDController


def _experiment(s, name):
    return ExperimentRepository(s).add(
        name=name, surface="checkout", owner="o", contract={}, spec={}, state="ramping"
    )


def _transition(s, exp, to, frm="ramping", actor="daemon"):
    AuditLogRepository(s).add(
        kind="state.transition",
        actor=actor,
        experiment_id=exp.id,
        payload={"from": frm, "to": to, "reason": "r"},
    )


def test_one_test_per_experiment_in_order_and_a_promotion_is_a_discovery(session_factory):
    with session_scope(session_factory) as s:
        a, b, c = _experiment(s, "a"), _experiment(s, "b"), _experiment(s, "c")
        _transition(s, a, "promoting")  # not terminal
        _transition(s, a, "promoted", frm="promoting")
        _transition(s, b, "reverted", actor="cli")  # a manual revert counts too
        _transition(s, a, "reverted", frm="promoted")  # a's later revert is not a new test
        _transition(s, c, "concluded", frm="holding")
        assert terminal_outcomes(s) == [True, False, False]


def test_a_restarted_controller_resumes_instead_of_respending_initial_wealth(session_factory):
    with session_scope(session_factory) as s:
        a, b = _experiment(s, "a"), _experiment(s, "b")
        _transition(s, a, "promoted", frm="promoting")
        _transition(s, b, "reverted")

        before_restart = LORDController()
        before_restart.record_outcome(True)
        before_restart.record_outcome(False)

        after_restart = LORDController()  # fresh process: in-memory state is gone
        sync_lord_from_history(s, after_restart)

        assert after_restart.tests_seen == 2
        assert after_restart.next_alpha() == before_restart.next_alpha()
        assert after_restart.next_alpha() < LORDController().next_alpha()


def test_syncing_twice_does_not_double_count(session_factory):
    with session_scope(session_factory) as s:
        _transition(s, _experiment(s, "a"), "reverted")
        lord = LORDController()
        sync_lord_from_history(s, lord)
        sync_lord_from_history(s, lord)
        assert lord.tests_seen == 1
