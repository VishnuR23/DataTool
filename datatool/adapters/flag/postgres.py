"""Postgres reference flag provider (ARCHITECTURE.md §11.1).

The "no vendor needed" path: assignment, allocation, and kill implemented directly
on the ``flag_assignments`` and ``flag_allocations`` tables, so a team can adopt
DataTool with nothing but the Postgres it already runs the controller on.

Assignment is deterministic: a unit's variant is a stable function of
``(experiment_id, unit_id)``, so the same user always sees the same variant and the
split matches the configured allocation. ARCHITECTURE.md §11.1 specifies
``hash(experiment_id + unit_id) % 10000 / 100``; we use SHA-256 rather than Python's
built-in ``hash`` because the built-in is salted per process and would reassign
units on every restart — determinism is the whole point. The resulting value in
``[0, 100)`` is mapped through the allocation buckets (control first, then variants
in name order).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from datatool.adapters import base as registry
from datatool.core.exceptions import AdapterError
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import (
    FlagAllocationRepository,
    FlagAssignmentRepository,
    VariantRepository,
)

ADAPTER_ID = "flag.postgres"
_CONTROL_ONLY = {"control": 100.0}


class PostgresFlagProvider:
    """FlagProvider backed by the bundled Postgres tables."""

    adapter_id = ADAPTER_ID

    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    # -- assignment --------------------------------------------------------- #

    def assign(self, experiment_id: UUID, unit_id: str) -> str:
        with session_scope(self._session_factory) as session:
            assignments = FlagAssignmentRepository(session)
            existing = assignments.get(experiment_id, unit_id)
            if existing is not None:
                variant = self._variant_by_id(session, experiment_id, existing.variant_id)
                return variant

            allocations = self._current_allocations(session, experiment_id)
            variant_name = _bucket(experiment_id, unit_id, allocations)

            variant = VariantRepository(session).get_by_name(experiment_id, variant_name)
            if variant is None:
                raise AdapterError(
                    f"allocation references variant {variant_name!r} which is not "
                    f"registered for experiment {experiment_id}"
                )
            assignments.add(experiment_id, unit_id, variant.id)
            return variant_name

    def get_assignment_counts(self, experiment_id: UUID, since: datetime) -> dict[str, int]:
        with session_scope(self._session_factory) as session:
            id_counts = FlagAssignmentRepository(session).counts_since(experiment_id, since)
            names = {v.id: v.name for v in VariantRepository(session).list_for(experiment_id)}
            return {names[vid]: count for vid, count in id_counts.items() if vid in names}

    # -- allocation --------------------------------------------------------- #

    def set_allocation(self, experiment_id: UUID, allocations: dict[str, float]) -> None:
        _validate_allocations(allocations)
        with session_scope(self._session_factory) as session:
            FlagAllocationRepository(session).upsert(experiment_id, allocations, killed=False)

    def kill(self, experiment_id: UUID) -> None:
        with session_scope(self._session_factory) as session:
            FlagAllocationRepository(session).upsert(
                experiment_id, dict(_CONTROL_ONLY), killed=True
            )

    def get_allocation(self, experiment_id: UUID) -> dict[str, float]:
        with session_scope(self._session_factory) as session:
            return self._current_allocations(session, experiment_id)

    # -- internals ---------------------------------------------------------- #

    def _current_allocations(self, session: Session, experiment_id: UUID) -> dict[str, float]:
        row = FlagAllocationRepository(session).get(experiment_id)
        if row is None or row.killed:
            return dict(_CONTROL_ONLY)
        return dict(row.allocations)

    def _variant_by_id(self, session: Session, experiment_id: UUID, variant_id: UUID) -> str:
        for variant in VariantRepository(session).list_for(experiment_id):
            if variant.id == variant_id:
                return variant.name
        raise AdapterError(f"assignment references unknown variant {variant_id}")


def _validate_allocations(allocations: dict[str, float]) -> None:
    if not allocations:
        raise AdapterError("allocations must be non-empty")
    if any(pct < 0 for pct in allocations.values()):
        raise AdapterError(f"allocation percentages must be non-negative: {allocations}")
    if abs(sum(allocations.values()) - 100.0) > 1e-6:
        raise AdapterError(f"allocations must sum to 100, got {sum(allocations.values())}")


def _bucket(experiment_id: UUID, unit_id: str, allocations: dict[str, float]) -> str:
    """Map a unit deterministically into an allocation bucket.

    SHA-256 of ``{experiment_id}:{unit_id}`` taken mod 10000 / 100 gives a stable
    value in ``[0, 100)``; the buckets are laid out control-first then by name so
    the layout (and thus every assignment) is reproducible.
    """
    digest = hashlib.sha256(f"{experiment_id}:{unit_id}".encode()).hexdigest()
    value = (int(digest, 16) % 10000) / 100.0  # in [0, 100)

    names = sorted(allocations, key=lambda name: (name != "control", name))
    cumulative = 0.0
    for name in names:
        cumulative += allocations[name]
        if value < cumulative:
            return name
    return names[-1]  # floating-point safety: land in the last bucket


def register() -> None:
    """Register the Postgres flag provider factory under ``flag.postgres``."""
    registry.register(ADAPTER_ID, PostgresFlagProvider)
