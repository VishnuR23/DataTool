"""VariantSource protocol (ARCHITECTURE.md §11.3).

A variant source turns a :class:`~datatool.core.models.VariantSpec` into the
adapter-specific payload stored with the variant — a file reference for the static
source, generated code for the LLM source, an external id for a third-party tool.
Generation is deliberately plugged in here, never in ``core/``: the controller's
job is to safely run an experiment, not to know how a variant came to exist
(ARCHITECTURE.md §11.3, §20).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from datatool.core.models import VariantSpec


@runtime_checkable
class VariantSource(Protocol):
    """Materialize a variant spec into its adapter-specific payload."""

    adapter_id: str

    def materialize(self, variant_spec: VariantSpec) -> dict:
        """Return the adapter-specific payload to store with the variant."""
        ...
