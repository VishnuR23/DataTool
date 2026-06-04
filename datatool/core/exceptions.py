"""Exception hierarchy for DataTool.

Every error the controller raises descends from :class:`DataToolError` so that
callers (the CLI, the API, the daemon) can catch the whole family at a boundary
and render it consistently. Subtrees mirror the architecture: contract errors,
state-machine errors, adapter errors.
"""

from __future__ import annotations


class DataToolError(Exception):
    """Base class for every error raised by DataTool."""


class ContractError(DataToolError):
    """Anything wrong with a trust contract: resolution, validation, or clamping."""


class ContractResolutionError(ContractError):
    """Raised when org/surface/experiment layers cannot be merged into a valid contract.

    Wraps the underlying cause (a deep-merge conflict or a Pydantic
    ``ValidationError``) so the CLI can point at the offending layer.
    """


class ClampError(ContractError):
    """Raised when a clamp is asked to do something incoherent.

    Clamping itself never fails for normal inputs — it is total by design (every
    desired allocation maps to a contract-legal one). This is reserved for
    programming errors, e.g. a ceiling outside ``[0, 100]``.
    """


class StateTransitionError(DataToolError):
    """Raised when an experiment is asked to make an illegal state transition.

    The set of legal transitions is defined by the state machine in
    ``core/state_machine.py`` (built in a later step).
    """
