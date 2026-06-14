"""Adapter registry and protocol conformance (ARCHITECTURE.md §11).

These pin the seam that keeps the control plane vendor-neutral: adapters register
under a stable id, the registry refuses silent overwrites and unknown lookups, and
a conforming implementation satisfies its Protocol structurally.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import pytest

from datatool.adapters import base as registry
from datatool.adapters.flag.base import FlagProvider
from datatool.adapters.notify.base import NotificationSink
from datatool.core.exceptions import AdapterError


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.clear_registry()
    yield
    registry.clear_registry()


def test_register_and_resolve_factory_by_id():
    """A registered factory is resolvable by its adapter id."""
    sentinel = object()
    registry.register("flag.fake", lambda: sentinel)
    assert registry.is_registered("flag.fake")
    assert registry.get_adapter_factory("flag.fake")() is sentinel
    assert registry.registered_adapter_ids() == ["flag.fake"]


def test_duplicate_registration_raises():
    """Re-registering an id raises rather than silently swapping the implementation."""
    registry.register("metrics.fake", lambda: None)
    with pytest.raises(AdapterError):
        registry.register("metrics.fake", lambda: None)


def test_unknown_adapter_lookup_raises():
    """Resolving an unregistered id raises with the list of what is available."""
    with pytest.raises(AdapterError):
        registry.get_adapter_factory("nope.missing")


def test_blank_adapter_id_raises():
    """A blank adapter id is rejected."""
    with pytest.raises(AdapterError):
        registry.register("   ", lambda: None)


class _FakeFlagProvider:
    adapter_id = "flag.memory"

    def __init__(self):
        self._alloc: dict[str, float] = {"control": 100.0}

    def assign(self, experiment_id: UUID, unit_id: str) -> str:
        return "control"

    def set_allocation(self, experiment_id: UUID, allocations: dict[str, float]) -> None:
        self._alloc = dict(allocations)

    def kill(self, experiment_id: UUID) -> None:
        self._alloc = {"control": 100.0}

    def get_allocation(self, experiment_id: UUID) -> dict[str, float]:
        return dict(self._alloc)

    def get_assignment_counts(self, experiment_id: UUID, since: datetime) -> dict[str, int]:
        return {"control": 0}


class _FakeNotifier:
    adapter_id = "notify.memory"

    def send(self, event_kind: str, payload: dict) -> None:
        pass


def test_conforming_implementation_satisfies_flag_provider_protocol():
    """A struct with the right methods satisfies FlagProvider (runtime_checkable)."""
    assert isinstance(_FakeFlagProvider(), FlagProvider)


def test_conforming_implementation_satisfies_notification_sink_protocol():
    """A struct with send() and adapter_id satisfies NotificationSink."""
    assert isinstance(_FakeNotifier(), NotificationSink)


def test_incomplete_implementation_does_not_satisfy_protocol():
    """A class missing required methods is not a FlagProvider."""

    class _Partial:
        adapter_id = "flag.partial"

        def assign(self, experiment_id: UUID, unit_id: str) -> str:
            return "control"

    assert not isinstance(_Partial(), FlagProvider)


def test_fake_flag_provider_round_trips_allocation():
    """The in-memory fake behaves: set then get returns what was set; kill resets."""
    fp = _FakeFlagProvider()
    exp = uuid4()
    fp.set_allocation(exp, {"control": 90.0, "treatment": 10.0})
    assert fp.get_allocation(exp) == {"control": 90.0, "treatment": 10.0}
    fp.kill(exp)
    assert fp.get_allocation(exp) == {"control": 100.0}
