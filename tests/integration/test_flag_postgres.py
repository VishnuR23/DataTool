"""Postgres reference flag adapter (ARCHITECTURE.md §11.1).

Pins the properties a flag provider must have: deterministic and stable
assignment, a split that tracks the configured allocation, allocation/kill
round-trips, and assignment counts for SRM.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from datatool.adapters import base as registry
from datatool.adapters.flag.base import FlagProvider
from datatool.adapters.flag.postgres import PostgresFlagProvider, register
from datatool.core.exceptions import AdapterError
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import ExperimentRepository, VariantRepository

EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


@pytest.fixture
def experiment(session_factory):
    """Create an experiment with control + treatment variants; return its id."""
    with session_scope(session_factory) as s:
        exp = ExperimentRepository(s).add(
            name="exp",
            surface="pricing-page",
            owner="growth",
            contract={"version": "1.0"},
            spec={},
            state="canary",
        )
        vr = VariantRepository(s)
        vr.add(experiment_id=exp.id, name="control", is_control=True, payload={})
        vr.add(experiment_id=exp.id, name="treatment", is_control=False, payload={})
        return exp.id


@pytest.fixture
def provider(session_factory):
    return PostgresFlagProvider(session_factory)


def test_provider_satisfies_flag_provider_protocol(provider):
    assert isinstance(provider, FlagProvider)


def test_assignment_is_deterministic_and_stable(provider, experiment):
    """The same unit always gets the same variant, even after the allocation changes."""
    provider.set_allocation(experiment, {"control": 50.0, "treatment": 50.0})
    first = provider.assign(experiment, "user-123")
    assert provider.assign(experiment, "user-123") == first  # repeatable

    # Reassigning the whole experiment must not move an already-assigned unit.
    provider.set_allocation(experiment, {"control": 1.0, "treatment": 99.0})
    assert provider.assign(experiment, "user-123") == first


def test_assignment_split_tracks_the_configured_allocation(provider, experiment):
    """Across many units a 90/10 allocation produces roughly a 90/10 split."""
    provider.set_allocation(experiment, {"control": 90.0, "treatment": 10.0})
    for i in range(2000):
        provider.assign(experiment, f"user-{i}")
    counts = provider.get_assignment_counts(experiment, EPOCH)
    total = counts["control"] + counts["treatment"]
    assert total == 2000
    # Deterministic hash; assert the split is within a few points of 90/10.
    assert 0.86 <= counts["control"] / total <= 0.94


def test_set_and_get_allocation_round_trip(provider, experiment):
    provider.set_allocation(experiment, {"control": 95.0, "treatment": 5.0})
    assert provider.get_allocation(experiment) == {"control": 95.0, "treatment": 5.0}


def test_allocation_not_summing_to_100_raises(provider, experiment):
    with pytest.raises(AdapterError):
        provider.set_allocation(experiment, {"control": 90.0, "treatment": 5.0})


def test_negative_allocation_raises(provider, experiment):
    with pytest.raises(AdapterError):
        provider.set_allocation(experiment, {"control": 110.0, "treatment": -10.0})


def test_unset_allocation_defaults_to_control_only(provider, experiment):
    assert provider.get_allocation(experiment) == {"control": 100.0}


def test_kill_routes_all_traffic_to_control(provider, experiment):
    provider.set_allocation(experiment, {"control": 50.0, "treatment": 50.0})
    provider.kill(experiment)
    assert provider.get_allocation(experiment) == {"control": 100.0}
    # Every unit now lands on control.
    assert {provider.assign(experiment, f"u{i}") for i in range(50)} == {"control"}


def test_assignment_counts_are_reported_per_variant(provider, experiment):
    provider.set_allocation(experiment, {"control": 50.0, "treatment": 50.0})
    for i in range(100):
        provider.assign(experiment, f"u{i}")
    counts = provider.get_assignment_counts(experiment, EPOCH)
    assert set(counts) <= {"control", "treatment"}
    assert sum(counts.values()) == 100


def test_allocation_referencing_unknown_variant_raises(session_factory):
    """If the allocation names a variant with no row, assignment fails loudly."""
    with session_scope(session_factory) as s:
        exp = ExperimentRepository(s).add(
            name="bare", surface="s", owner="o", contract={}, spec={}, state="canary"
        )
        VariantRepository(s).add(experiment_id=exp.id, name="control", is_control=True, payload={})
        eid = exp.id
    provider = PostgresFlagProvider(session_factory)
    provider.set_allocation(eid, {"control": 50.0, "treatment": 50.0})  # treatment has no row
    # A unit that buckets into 'treatment' cannot be assigned.
    with pytest.raises(AdapterError):
        for i in range(200):
            provider.assign(eid, f"u{i}")


def test_register_adds_factory_to_registry():
    registry.clear_registry()
    try:
        register()
        assert registry.is_registered("flag.postgres")
        assert registry.get_adapter_factory("flag.postgres") is PostgresFlagProvider
    finally:
        registry.clear_registry()
