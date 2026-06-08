"""Trust-contract resolution and clamping.

This module is the operational half of the project's spine. Two responsibilities:

1. **Resolution** (ARCHITECTURE.md §7). Contracts compose in three layers — org
   defaults, surface defaults, and the experiment's own override — deep-merged
   with the more specific layer winning. :func:`resolve_contract` performs that
   merge and validates the result into a :class:`TrustContract`.

2. **Clamping** (ARCHITECTURE.md §9.3, §22). Every allocation the orchestrator
   wants to apply is clamped to the contract's autonomous ceiling, and every
   clamp is reported so it can be logged. :func:`clamp_to_ceiling` is the pure,
   total function that defines that semantics. Keeping it here — not in the
   orchestrator — means there is exactly one place state-changing magnitudes are
   bounded, which is the invariant CLAUDE.md requires.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from datatool.core.exceptions import ClampError, ContractResolutionError
from datatool.core.models import TrustContract

# --------------------------------------------------------------------------- #
# Resolution (org -> surface -> experiment)
# --------------------------------------------------------------------------- #


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` onto ``base``, returning a new dict.

    Merge semantics (deliberately chosen for contract inheritance):
    - When the same key holds a dict in *both* layers, merge recursively.
    - Otherwise ``override`` wins outright — including for lists and for an
      explicit ``None``. Lists are replaced wholesale, never concatenated or
      merged element-wise: a more specific layer that lists guardrails means
      "use exactly these", not "append to the inherited ones".

    Neither input is mutated.
    """
    result = copy.deepcopy(base)
    for key, override_value in override.items():
        base_value = result.get(key)
        if isinstance(base_value, dict) and isinstance(override_value, dict):
            result[key] = deep_merge(base_value, override_value)
        else:
            result[key] = copy.deepcopy(override_value)
    return result


def merge_contract_layers(*layers: dict | None) -> dict:
    """Deep-merge contract layers in precedence order (later layers win).

    ``None`` layers are skipped, so callers can pass missing org or surface
    defaults directly. Returns the merged dict *before* validation — useful for
    surfacing the effective contract (e.g. ``datatool show``) and for tests.
    """
    merged: dict[str, Any] = {}
    for layer in layers:
        if layer is None:
            continue
        if not isinstance(layer, dict):
            raise ContractResolutionError(
                f"contract layer must be a mapping, got {type(layer).__name__}"
            )
        merged = deep_merge(merged, layer)
    return merged


def resolve_contract(
    org_defaults: dict | None = None,
    surface_defaults: dict | None = None,
    experiment: dict | None = None,
) -> TrustContract:
    """Resolve the effective trust contract from its three inheritance layers.

    Precedence, most specific last: ``org_defaults`` < ``surface_defaults`` <
    ``experiment`` (ARCHITECTURE.md §7). Each argument is the ``contract``-shaped
    mapping for that layer, or ``None`` if absent.

    Raises :class:`ContractResolutionError` if the merged result is not a valid
    contract; the underlying Pydantic ``ValidationError`` is chained so the CLI
    can show exactly which field failed.

    Note: the trust ledger's effective-contract *deltas* (§9.4) are applied by a
    later layer, not here — this function resolves the on-disk inheritance only.
    """
    merged = merge_contract_layers(org_defaults, surface_defaults, experiment)
    if not merged:
        raise ContractResolutionError(
            "no contract data to resolve: all of org/surface/experiment were empty"
        )
    try:
        return TrustContract.model_validate(merged)
    except ValidationError as exc:
        raise ContractResolutionError(f"resolved contract failed validation:\n{exc}") from exc


# --------------------------------------------------------------------------- #
# Clamping (autonomous-authority ceiling)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ClampResult:
    """The outcome of clamping a desired allocation to the contract.

    Mirrors the ``actions.clamped`` / ``actions.clamp_reason`` columns (§5): the
    orchestrator records exactly these three values on every allocation action.
    """

    value: float  # the contract-legal allocation to actually apply
    clamped: bool  # True iff the desired value was reduced
    reason: str | None  # human-readable reason when clamped, else None


def clamp_to_ceiling(desired_pct: float, contract: TrustContract) -> ClampResult:
    """Clamp a desired allocation percentage to the contract's autonomous ceiling.

    Implements the clamp in ARCHITECTURE.md §9.3: if ``desired_pct`` exceeds
    ``contract.allocation.max_autonomous_pct`` the controller may not apply it
    autonomously, so it is reduced to the ceiling and the result is flagged as
    clamped with a reason. Otherwise the desired value passes through unchanged.

    This function is *total* for any allocation in ``[0, 100]`` — it never raises
    for ordinary inputs. A :class:`ClampError` indicates a programming error: a
    desired value outside ``[0, 100]``, where percentages are undefined.
    """
    if not (0 <= desired_pct <= 100):
        raise ClampError(f"desired allocation {desired_pct} is not a percentage in [0, 100]")
    ceiling = contract.allocation.max_autonomous_pct
    if desired_pct > ceiling:
        return ClampResult(
            value=ceiling,
            clamped=True,
            reason=(
                f"desired {desired_pct:g}% exceeds autonomous ceiling "
                f"max_autonomous_pct={ceiling:g}%; clamped to ceiling"
            ),
        )
    return ClampResult(value=desired_pct, clamped=False, reason=None)
