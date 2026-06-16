"""Persistence layer round-trips and invariants (ARCHITECTURE.md §5).

Exercises the ORM + repositories against a real (SQLite) database: schema creation,
each repository's insert/query, the unique constraints, the trust-event aggregates,
the flag tables, and the transactional rollback behavior of session_scope.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from datatool.persistence import models as m
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import (
    ActionRepository,
    AuditLogRepository,
    DecisionRepository,
    ExperimentRepository,
    FlagAllocationRepository,
    FlagAssignmentRepository,
    GuardrailEvaluationRepository,
    TrustEventRepository,
    VariantRepository,
)


def _add_experiment(session, *, name="exp", surface="pricing-page", state="proposed"):
    return ExperimentRepository(session).add(
        name=name,
        surface=surface,
        owner="growth",
        contract={"version": "1.0"},
        spec={"variants": []},
        state=state,
    )


def test_init_db_creates_all_eleven_tables(session_factory):
    assert set(m.Base.metadata.tables) == {
        "experiments",
        "variants",
        "samples",
        "decisions",
        "actions",
        "guardrail_evaluations",
        "trust_events",
        "audit_log",
        "flag_assignments",
        "flag_allocations",
        "llm_variant_cache",
    }


# --------------------------------------------------------------------------- #
# Experiments
# --------------------------------------------------------------------------- #


def test_experiment_add_get_and_lookup_by_name(session_factory):
    with session_scope(session_factory) as s:
        exp = _add_experiment(s, name="pricing-headline")
        eid = exp.id
    with session_scope(session_factory) as s:
        repo = ExperimentRepository(s)
        assert repo.get(eid).name == "pricing-headline"
        assert repo.get_by_name("pricing-headline").id == eid


def test_experiment_list_filters_by_state_and_surface(session_factory):
    with session_scope(session_factory) as s:
        _add_experiment(s, name="a", surface="pricing-page", state="canary")
        _add_experiment(s, name="b", surface="pricing-page", state="reverted")
        _add_experiment(s, name="c", surface="home", state="canary")
    with session_scope(session_factory) as s:
        repo = ExperimentRepository(s)
        assert {e.name for e in repo.list(state="canary")} == {"a", "c"}
        assert {e.name for e in repo.list(surface="pricing-page")} == {"a", "b"}
        assert {e.name for e in repo.list(state="canary", surface="pricing-page")} == {"a"}


def test_experiment_state_advances(session_factory):
    with session_scope(session_factory) as s:
        eid = _add_experiment(s, state="proposed").id
    with session_scope(session_factory) as s:
        ExperimentRepository(s).set_state(eid, "canary")
    with session_scope(session_factory) as s:
        assert ExperimentRepository(s).get(eid).state == "canary"


def test_duplicate_experiment_name_violates_unique_constraint(session_factory):
    with pytest.raises(IntegrityError):
        with session_scope(session_factory) as s:
            _add_experiment(s, name="dup")
            _add_experiment(s, name="dup")


# --------------------------------------------------------------------------- #
# Variants
# --------------------------------------------------------------------------- #


def test_variant_add_list_and_unique_per_experiment(session_factory):
    with session_scope(session_factory) as s:
        exp = _add_experiment(s)
        vr = VariantRepository(s)
        vr.add(experiment_id=exp.id, name="control", is_control=True, payload={})
        vr.add(experiment_id=exp.id, name="treatment", is_control=False, payload={"ref": "v2"})
        assert {v.name for v in vr.list_for(exp.id)} == {"control", "treatment"}
        assert vr.get_by_name(exp.id, "control").is_control is True

    with pytest.raises(IntegrityError):
        with session_scope(session_factory) as s:
            exp = ExperimentRepository(s).get_by_name("exp")
            vr = VariantRepository(s)
            vr.add(experiment_id=exp.id, name="control", is_control=True, payload={})
            vr.add(experiment_id=exp.id, name="control", is_control=False, payload={})


# --------------------------------------------------------------------------- #
# Append-only tables
# --------------------------------------------------------------------------- #


def test_decisions_and_actions_insert_and_query(session_factory):
    with session_scope(session_factory) as s:
        exp = _add_experiment(s)
        d = DecisionRepository(s).add(
            experiment_id=exp.id,
            kind="ramp",
            reason="cs decisive",
            inputs={"n": 100},
            outputs={"cs_lower": 0.02},
        )
        ActionRepository(s).add(
            experiment_id=exp.id,
            decision_id=d.id,
            kind="allocate",
            adapter="flag.postgres",
            payload={"allocations": {"control": 95, "treatment": 5}},
            clamped=True,
            clamp_reason="ceiling",
            succeeded=True,
            error=None,
        )
        eid = exp.id
    with session_scope(session_factory) as s:
        assert DecisionRepository(s).list_for(eid)[0].kind == "ramp"
        action = ActionRepository(s).list_for(eid)[0]
        assert action.clamped is True and action.adapter == "flag.postgres"


def test_guardrail_evaluations_insert_and_query(session_factory):
    with session_scope(session_factory) as s:
        exp = _add_experiment(s)
        GuardrailEvaluationRepository(s).add(
            experiment_id=exp.id,
            guardrail_name="error_rate",
            breached=True,
            value=0.3,
            threshold=0.2,
            severity="critical",
            consecutive=2,
        )
        eid = exp.id
    with session_scope(session_factory) as s:
        ev = GuardrailEvaluationRepository(s).list_for(eid)[0]
        assert ev.guardrail_name == "error_rate" and ev.consecutive == 2


def test_append_only_repos_expose_no_mutators():
    """The append-only repositories provide insert/query only — no update/delete.

    Enforces the immutable-audit-log rule structurally: a mutator simply doesn't
    exist to call.
    """
    for repo in (
        DecisionRepository,
        ActionRepository,
        GuardrailEvaluationRepository,
        TrustEventRepository,
        AuditLogRepository,
    ):
        names = set(dir(repo))
        assert not (names & {"update", "delete", "set_state", "remove"})


def test_trust_event_counts_by_kind(session_factory):
    with session_scope(session_factory) as s:
        exp = _add_experiment(s, surface="pricing-page")
        ter = TrustEventRepository(s)
        for _ in range(3):
            ter.add(
                surface="pricing-page",
                kind="clean_promotion",
                experiment_id=exp.id,
                delta=None,
                new_state={},
                reason="ok",
            )
        ter.add(
            surface="pricing-page",
            kind="false_positive_ship",
            experiment_id=exp.id,
            delta=None,
            new_state={},
            reason="bad",
        )
    with session_scope(session_factory) as s:
        ter = TrustEventRepository(s)
        assert ter.count("pricing-page", "clean_promotion") == 3
        assert ter.count("pricing-page", "false_positive_ship") == 1
        assert ter.count("home", "clean_promotion") == 0


def test_audit_log_records_and_lists_in_order(session_factory):
    with session_scope(session_factory) as s:
        exp = _add_experiment(s)
        alr = AuditLogRepository(s)
        alr.add(kind="experiment.registered", actor="system", experiment_id=exp.id, payload={})
        alr.add(
            kind="state.transition", actor="system", experiment_id=exp.id, payload={"to": "canary"}
        )
        eid = exp.id
    with session_scope(session_factory) as s:
        entries = AuditLogRepository(s).list_for(eid)
        assert [e.kind for e in entries] == ["experiment.registered", "state.transition"]


# --------------------------------------------------------------------------- #
# Flag tables
# --------------------------------------------------------------------------- #


def test_flag_allocation_upsert_and_get(session_factory):
    with session_scope(session_factory) as s:
        exp = _add_experiment(s)
        far = FlagAllocationRepository(s)
        far.upsert(exp.id, {"control": 99.0, "treatment": 1.0})
        far.upsert(exp.id, {"control": 95.0, "treatment": 5.0})  # update
        eid = exp.id
    with session_scope(session_factory) as s:
        row = FlagAllocationRepository(s).get(eid)
        assert row.allocations == {"control": 95.0, "treatment": 5.0}
        assert row.killed is False


def test_flag_assignment_counts_since(session_factory):
    with session_scope(session_factory) as s:
        exp = _add_experiment(s)
        vr = VariantRepository(s)
        control = vr.add(experiment_id=exp.id, name="control", is_control=True, payload={})
        treatment = vr.add(experiment_id=exp.id, name="treatment", is_control=False, payload={})
        far = FlagAssignmentRepository(s)
        for i in range(6):
            far.add(exp.id, f"u{i}", control.id)
        for i in range(4):
            far.add(exp.id, f"t{i}", treatment.id)
        eid, cid, tid = exp.id, control.id, treatment.id
    with session_scope(session_factory) as s:
        counts = FlagAssignmentRepository(s).counts_since(eid, datetime(2000, 1, 1, tzinfo=UTC))
        assert counts[cid] == 6
        assert counts[tid] == 4


# --------------------------------------------------------------------------- #
# Transaction handling
# --------------------------------------------------------------------------- #


def test_session_scope_rolls_back_on_error(session_factory):
    with pytest.raises(RuntimeError):
        with session_scope(session_factory) as s:
            _add_experiment(s, name="will-rollback")
            raise RuntimeError("boom")
    with session_scope(session_factory) as s:
        assert ExperimentRepository(s).get_by_name("will-rollback") is None


def test_last_revert_at_returns_most_recent_reverted_experiment(session_factory):
    with session_scope(session_factory) as s:
        _add_experiment(s, name="r1", surface="pricing-page", state="reverted")
        _add_experiment(s, name="active", surface="pricing-page", state="canary")
    with session_scope(session_factory) as s:
        repo = ExperimentRepository(s)
        assert repo.last_revert_at("pricing-page") is not None
        assert repo.last_revert_at("home") is None
