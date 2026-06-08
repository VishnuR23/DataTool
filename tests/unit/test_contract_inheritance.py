"""Three-layer contract resolution: org -> surface -> experiment.

ARCHITECTURE.md §7 names this file and the property it pins: deep merge with
experiment > surface > org. These tests check provenance (which layer a field
ends up coming from), the merge mechanics (dicts deep-merge, lists replace), and
the error behaviour when the merged result is invalid.
"""

from __future__ import annotations

import pytest

from datatool.core.contract import deep_merge, merge_contract_layers, resolve_contract
from datatool.core.exceptions import ContractResolutionError
from datatool.core.models import TrustContract

from .conftest import CONFIG_DIR, load_yaml, make_contract_dict

# --------------------------------------------------------------------------- #
# deep_merge mechanics
# --------------------------------------------------------------------------- #


def test_deep_merge_combines_nested_dicts():
    base = {"scope": {"assignment_unit": "user", "holdout_pct": 0.0}}
    override = {"scope": {"holdout_pct": 5.0, "exclusivity_group": "pricing"}}
    merged = deep_merge(base, override)
    assert merged["scope"] == {
        "assignment_unit": "user",  # inherited from base
        "holdout_pct": 5.0,  # overridden
        "exclusivity_group": "pricing",  # added
    }


def test_deep_merge_replaces_lists_wholesale():
    # A more specific layer's list means "use exactly these", not "append".
    base = {"allocation": {"ramp_schedule": [1, 2.5, 5, 10, 25]}}
    override = {"allocation": {"ramp_schedule": [1, 5]}}
    assert deep_merge(base, override)["allocation"]["ramp_schedule"] == [1, 5]


def test_deep_merge_override_wins_for_scalars_and_explicit_none():
    base = {"a": 1, "b": 2}
    override = {"a": 99, "b": None}
    assert deep_merge(base, override) == {"a": 99, "b": None}


def test_deep_merge_does_not_mutate_inputs():
    base = {"x": {"y": 1}}
    override = {"x": {"z": 2}}
    deep_merge(base, override)
    assert base == {"x": {"y": 1}}
    assert override == {"x": {"z": 2}}


def test_merge_contract_layers_skips_none_layers():
    merged = merge_contract_layers(None, {"a": 1}, None, {"b": 2})
    assert merged == {"a": 1, "b": 2}


# --------------------------------------------------------------------------- #
# resolve_contract precedence (experiment > surface > org)
# --------------------------------------------------------------------------- #


def test_experiment_layer_overrides_surface_and_org():
    org = make_contract_dict()
    org["allocation"]["max_autonomous_pct"] = 5.0
    surface = {"allocation": {"max_autonomous_pct": 10.0}}
    experiment = {"allocation": {"max_autonomous_pct": 15.0}}
    resolved = resolve_contract(org, surface, experiment)
    assert resolved.allocation.max_autonomous_pct == 15.0


def test_surface_layer_overrides_org_when_experiment_silent():
    org = make_contract_dict()
    org["allocation"]["max_autonomous_pct"] = 5.0
    surface = {"allocation": {"max_autonomous_pct": 10.0}}
    resolved = resolve_contract(org, surface, experiment=None)
    assert resolved.allocation.max_autonomous_pct == 10.0


def test_org_values_survive_when_no_more_specific_layer_sets_them():
    org = make_contract_dict()
    org["statistics"]["alpha"] = 0.01
    resolved = resolve_contract(org, {"allocation": {"max_autonomous_pct": 8.0}}, None)
    assert resolved.statistics.alpha == 0.01
    assert resolved.allocation.max_autonomous_pct == 8.0


def test_nested_scope_fields_merge_across_layers():
    org = make_contract_dict()
    org["scope"] = {"assignment_unit": "user", "holdout_pct": 0.0}
    surface = {"scope": {"holdout_pct": 5.0, "forbidden_components": ["PaymentForm"]}}
    resolved = resolve_contract(org, surface, None)
    assert resolved.scope.assignment_unit == "user"  # from org
    assert resolved.scope.holdout_pct == 5.0  # from surface
    assert resolved.scope.forbidden_components == ["PaymentForm"]  # from surface


def test_resolution_yields_a_trust_contract():
    resolved = resolve_contract(make_contract_dict())
    assert isinstance(resolved, TrustContract)


# --------------------------------------------------------------------------- #
# resolve_contract error behaviour
# --------------------------------------------------------------------------- #


def test_empty_resolution_raises():
    with pytest.raises(ContractResolutionError):
        resolve_contract(None, None, None)


def test_invalid_merged_contract_raises_resolution_error():
    org = make_contract_dict()
    org["statistics"]["alpha"] = 5.0  # out of (0, 1)
    with pytest.raises(ContractResolutionError):
        resolve_contract(org)


def test_non_mapping_layer_raises():
    with pytest.raises(ContractResolutionError):
        resolve_contract(["not", "a", "mapping"])  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Resolution from the real config layer files (ARCHITECTURE.md §15)
# --------------------------------------------------------------------------- #


def test_resolution_from_real_org_and_surface_files():
    org = load_yaml(CONFIG_DIR / "org_defaults.yaml")
    surface = load_yaml(CONFIG_DIR / "surfaces" / "pricing-page.yaml")
    # The experiment supplies what org/surface intentionally omit: goal + guardrails.
    experiment = {
        "goal": {"source": "metrics.posthog", "metric": "signup_rate", "direction": "increase"},
        "guardrails": [
            {
                "name": "error_rate",
                "source": "metrics.posthog",
                "metric": "$pageview_error_rate",
                "threshold": {"type": "relative_increase", "value": 0.2},
                "window": "PT10M",
            }
        ],
    }
    resolved = resolve_contract(org, surface, experiment)

    # Provenance across the three layers:
    assert resolved.statistics.alpha == 0.05  # org default
    assert resolved.allocation.full_rollout_requires == "human_approval"  # org default
    assert resolved.scope.holdout_pct == 5.0  # surface override
    assert resolved.scope.exclusivity_group == "pricing"  # surface
    assert resolved.scope.forbidden_components == [
        "PaymentForm",
        "LegalDisclaimer",
        "TaxCalculator",
    ]
    assert resolved.authorization.grant_autonomy_increase == ["growth-lead"]  # surface
    assert {g.name for g in resolved.guardrails} == {"error_rate"}  # experiment
