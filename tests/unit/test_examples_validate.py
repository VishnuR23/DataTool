"""The shipped example and config files must parse against the real models.

These guard against drift between ARCHITECTURE.md §7's canonical contract and the
schema: if a field is renamed or a constraint tightened, the example breaks here.
"""

from __future__ import annotations

from datatool.core.contract import resolve_contract
from datatool.core.models import ExperimentSpec, Severity, ThresholdType

from .conftest import EXAMPLES_DIR, load_yaml


def _load_pricing_page() -> dict:
    return load_yaml(EXAMPLES_DIR / "pricing_page.yaml")


def test_canonical_example_parses_to_experiment_spec():
    raw = _load_pricing_page()
    spec = ExperimentSpec(
        name=raw["experiment"],
        surface=raw["surface"],
        owner=raw["owner"],
        description=raw.get("description"),
        variants=raw["variants"],
        contract=raw["contract"],
    )
    assert spec.name == "pricing-headline-clarity"
    assert len(spec.variants) == 2
    assert sum(v.is_control for v in spec.variants) == 1


def test_canonical_contract_has_expected_guardrails():
    contract = resolve_contract(_load_pricing_page()["contract"])
    by_name = {g.name: g for g in contract.guardrails}
    assert set(by_name) == {"error_rate", "latency_p95", "checkout_completion"}
    assert by_name["error_rate"].severity is Severity.CRITICAL
    assert by_name["latency_p95"].threshold.type is ThresholdType.ABSOLUTE
    assert by_name["checkout_completion"].consecutive_breaches_to_trip == 3


def test_canonical_contract_scope_and_stats():
    contract = resolve_contract(_load_pricing_page()["contract"])
    assert contract.scope.holdout_pct == 5.0
    assert contract.scope.exclusivity_group == "pricing"
    assert contract.allocation.max_autonomous_pct == 5.0
    assert contract.allocation.full_rollout_requires == "human_approval"
    assert contract.statistics.alpha == 0.05
    assert contract.goal.minimum_detectable_effect == 0.02
    assert len(contract.secondary_metrics) == 1
