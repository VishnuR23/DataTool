"""FlagProvider protocol (ARCHITECTURE.md §11.1).

A flag provider is the execution-plane handle the orchestrator uses to actually
change what users see: assign units to variants, set allocation percentages, and
kill an experiment back to control. The controller depends only on this Protocol,
never on a concrete vendor (LaunchDarkly, Statsig, or the bundled Postgres
reference impl), so the same control logic drives any of them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class FlagProvider(Protocol):
    """Assign units, control allocation, and kill an experiment on the flag plane."""

    adapter_id: str

    def assign(self, experiment_id: UUID, unit_id: str) -> str:
        """Return the variant name this unit is assigned to (deterministic per unit)."""
        ...

    def set_allocation(self, experiment_id: UUID, allocations: dict[str, float]) -> None:
        """Set allocation percentages per variant. The values must sum to 100."""
        ...

    def kill(self, experiment_id: UUID) -> None:
        """Immediately route all traffic to control (control: 100)."""
        ...

    def get_allocation(self, experiment_id: UUID) -> dict[str, float]:
        """Return the current allocation percentages per variant."""
        ...

    def get_assignment_counts(self, experiment_id: UUID, since: datetime) -> dict[str, int]:
        """Return how many units have been assigned to each variant since ``since`` (for SRM)."""
        ...
