"""Graduation rules are evaluated after every terminal action (ARCHITECTURE.md §9.4).

A fired rule writes a trust event whose delta the effective contract picks up at
resolution time; the written contract is never modified. Cooldowns persist across
evaluations via the trust events themselves.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from datatool.control.decision_engine import ControlDecision
from datatool.control.graduation import record_graduation
from datatool.control.loader import load_effective_contract
from datatool.control.orchestrator import OrchestrationResult, TrustEventRecord
from datatool.control.persist import persist_outcome
from datatool.core.models import (
    Allocation,
    DecisionKind,
    Graduation,
    GraduationRule,
    State,
)
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import (
    AuditLogRepository,
    ExperimentRepository,
    TrustEventRepository,
)
from tests.unit.conftest import make_contract

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
SURFACE = "pricing-page"

GRADUATE = GraduationRule(
    when={"clean_promotions_on_surface": ">=3", "false_positive_ships": 0},
    action={"field": "max_autonomous_pct", "op": "increase", "by": 5, "up_to": 25},
)
DEMOTE = GraduationRule(
    when={"false_positive_ships": ">=1"},
    action={"field": "max_autonomous_pct", "op": "decrease", "by": 5, "cooldown": "P30D"},
)


def _experiment(session, name="exp", rules=(GRADUATE, DEMOTE), max_pct=10.0):
    contract = make_contract(
        allocation=Allocation(ramp_schedule=[1, 5, 10], max_autonomous_pct=max_pct),
        graduation=Graduation(rules=list(rules)),
    )
    return ExperimentRepository(session).add(
        name=name,
        surface=SURFACE,
        owner="o",
        contract=contract.model_dump(mode="json"),
        spec={},
        state="promoted",
    )


def _trust(session, exp, kind):
    TrustEventRepository(session).add(
        surface=SURFACE, kind=kind, experiment_id=exp.id, delta=None, new_state={}, reason="r"
    )


def _effective_max(session, exp_id) -> float:
    exp = ExperimentRepository(session).get(exp_id)
    return load_effective_contract(session, exp).allocation.max_autonomous_pct


def test_third_clean_promotion_raises_effective_autonomy_and_is_audited(session_factory):
    with session_scope(session_factory) as s:
        exp = _experiment(s)
        for _ in range(3):
            _trust(s, exp, "clean_promotion")
        outcomes = record_graduation(s, exp.id, actor="daemon", now=NOW)

        assert [(o.kind, o.delta) for o in outcomes] == [("graduate", 5.0)]
        assert _effective_max(s, exp.id) == 15.0  # written contract says 10
        assert (
            ExperimentRepository(s).get(exp.id).contract["allocation"]["max_autonomous_pct"] == 10.0
        )  # the written contract is untouched
        kinds = [row.kind for row in AuditLogRepository(s).list_for(exp.id)]
        assert "trust.graduated" in kinds


def test_no_rule_fires_below_its_threshold(session_factory):
    with session_scope(session_factory) as s:
        exp = _experiment(s)
        for _ in range(2):
            _trust(s, exp, "clean_promotion")
        before = len(TrustEventRepository(s).list_for_surface(SURFACE))
        assert record_graduation(s, exp.id, actor="daemon", now=NOW) == []
        assert len(TrustEventRepository(s).list_for_surface(SURFACE)) == before


def test_demotion_cooldown_holds_across_evaluations(session_factory):
    with session_scope(session_factory) as s:
        exp = _experiment(s)
        _trust(s, exp, "false_positive_ship")

        first = record_graduation(s, exp.id, actor="daemon", now=NOW)
        assert [(o.kind, o.delta) for o in first] == [("demote", -5.0)]
        assert _effective_max(s, exp.id) == 5.0

        # Still inside the P30D cooldown: the same rule may not fire again.
        assert record_graduation(s, exp.id, actor="daemon", now=NOW + timedelta(days=1)) == []

        # After the cooldown it fires again.
        later = record_graduation(s, exp.id, actor="daemon", now=NOW + timedelta(days=31))
        assert [o.kind for o in later] == ["demote"]


def test_a_contract_without_rules_never_changes_autonomy(session_factory):
    with session_scope(session_factory) as s:
        exp = _experiment(s, rules=())
        for _ in range(5):
            _trust(s, exp, "clean_promotion")
        assert record_graduation(s, exp.id, actor="daemon", now=NOW) == []
        assert _effective_max(s, exp.id) == 10.0


def test_persisting_a_promotion_evaluates_graduation(session_factory):
    with session_scope(session_factory) as s:
        exp = _experiment(s)
        exp.state = "ramping"
        for _ in range(2):
            _trust(s, exp, "clean_promotion")

        decision = ControlDecision(
            kind=DecisionKind.PROMOTE,
            target_state=State.PROMOTED,
            reason="goal cs clears zero",
            structured_reason={},
            guardrail_evaluations=[],
            updated_breach_counts={},
        )
        result = OrchestrationResult(
            transitions=[
                (State.RAMPING, State.PROMOTING, "promotion started"),
                (State.PROMOTING, State.PROMOTED, "promoted"),
            ],
            trust_event=TrustEventRecord(
                surface=SURFACE, kind="clean_promotion", experiment_id=exp.id, reason="r"
            ),
        )
        persist_outcome(s, exp.id, decision, result, adapter_id="flag.postgres", actor="daemon")

        # The third clean promotion was just recorded, so the graduate rule fired.
        assert _effective_max(s, exp.id) == 15.0
