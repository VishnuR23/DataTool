"""`datatool register` (ARCHITECTURE.md §12).

Registration parses the experiment YAML, resolves the three contract layers, validates
the spec, and persists the experiment + variants + an audit row, leaving it proposed
with everyone on control.
"""

from __future__ import annotations

from datatool.core.models import State, TrustContract
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import (
    AuditLogRepository,
    ExperimentRepository,
    FlagAllocationRepository,
    VariantRepository,
)


def test_register_persists_experiment_variants_and_audit(cli_env):
    result = cli_env.invoke("register", cli_env.example())
    assert result.exit_code == 0, result.output

    with session_scope(cli_env.factory()) as s:
        exp = ExperimentRepository(s).get_by_name("pricing-headline-clarity")
        assert exp is not None
        assert exp.surface == "pricing-page"
        assert exp.state == State.PROPOSED.value
        variants = {v.name for v in VariantRepository(s).list_for(exp.id)}
        assert variants == {"control", "treatment"}
        audit = [a.kind for a in AuditLogRepository(s).list_for(exp.id)]
        assert "experiment.registered" in audit


def test_register_stores_the_resolved_contract(cli_env):
    cli_env.invoke("register", cli_env.example())
    with session_scope(cli_env.factory()) as s:
        exp = ExperimentRepository(s).get_by_name("pricing-headline-clarity")
        # The stored contract round-trips back into a valid TrustContract.
        contract = TrustContract.model_validate(exp.contract)
        assert len(contract.guardrails) == 3
        assert contract.goal.metric == "signup_completion_rate"


def test_register_sets_initial_allocation_to_control(cli_env):
    cli_env.invoke("register", cli_env.example())
    with session_scope(cli_env.factory()) as s:
        exp = ExperimentRepository(s).get_by_name("pricing-headline-clarity")
        allocation = FlagAllocationRepository(s).get(exp.id)
        assert allocation.allocations == {"control": 100.0, "treatment": 0.0}


def test_duplicate_registration_fails(cli_env):
    assert cli_env.invoke("register", cli_env.example()).exit_code == 0
    second = cli_env.invoke("register", cli_env.example())
    assert second.exit_code == 1
    assert "already registered" in second.output


def test_invalid_yaml_fails(cli_env, tmp_path):
    bad = tmp_path / "bad.yaml"
    # Missing required top-level keys (surface/owner/variants/contract).
    bad.write_text("experiment: broken\n")
    result = cli_env.invoke("register", str(bad))
    assert result.exit_code == 1
