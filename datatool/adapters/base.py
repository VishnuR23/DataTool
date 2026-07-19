"""Adapter registry (ARCHITECTURE.md §11).

Every external integration — flag providers, metrics sources, notification sinks,
variant sources — is a PEP 544 Protocol (defined in the per-category ``base.py``
files) plus a concrete implementation that registers itself here under a stable
adapter id like ``flag.postgres`` or ``metrics.csv``.

The registry is the seam that keeps the control plane vendor-neutral: ``core/`` and
``control/`` resolve adapters by id at runtime and never import a vendor module
directly (CLAUDE.md). Any integrator can delete every concrete adapter, register their
own, and the brain is unchanged.

Implementations register a *factory* (a callable that builds a configured instance)
rather than an instance, so construction — opening connections, reading env vars —
happens lazily when the daemon wires things up, not at import time.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from datatool.core.exceptions import AdapterError

# adapter_id -> factory callable. A flat namespace; ids are already dotted by
# category (e.g. "metrics.posthog"), so collisions across categories cannot occur.
_REGISTRY: dict[str, Callable[..., Any]] = {}


def register(adapter_id: str, factory: Callable[..., Any]) -> None:
    """Register an adapter factory under ``adapter_id``.

    Raises :class:`AdapterError` if the id is blank or already taken — a silent
    overwrite could swap a vendor implementation out from under the controller.
    """
    if not adapter_id or not adapter_id.strip():
        raise AdapterError("adapter_id must be a non-empty string")
    if adapter_id in _REGISTRY:
        raise AdapterError(f"adapter '{adapter_id}' is already registered")
    _REGISTRY[adapter_id] = factory


def get_adapter_factory(adapter_id: str) -> Callable[..., Any]:
    """Return the factory registered under ``adapter_id``.

    Raises :class:`AdapterError` if nothing is registered there.
    """
    try:
        return _REGISTRY[adapter_id]
    except KeyError:
        raise AdapterError(
            f"no adapter registered under '{adapter_id}'; registered: {registered_adapter_ids()}"
        ) from None


def is_registered(adapter_id: str) -> bool:
    """Whether an adapter is registered under ``adapter_id``."""
    return adapter_id in _REGISTRY


def registered_adapter_ids() -> list[str]:
    """All registered adapter ids, sorted."""
    return sorted(_REGISTRY)


def clear_registry() -> None:
    """Remove all registrations. Intended for test isolation."""
    _REGISTRY.clear()
