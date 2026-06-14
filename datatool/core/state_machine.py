"""Experiment lifecycle state machine (ARCHITECTURE.md §10).

The state machine is the structural backbone of the controller: every state change
an experiment undergoes must be a *declared legal* transition, and any attempt to
make an illegal one raises rather than silently corrupting state. This is what lets
the rest of the system trust that an experiment in ``PROMOTED`` was actually
ramped, held, and promoted in order — not teleported there by a bug.

Legal transitions encode the diagram in §10:

    PROPOSED  -> CANARY (start) | REVERTED | CONCLUDED
    CANARY    -> RAMPING | HOLDING | PROMOTING | REVERTED | CONCLUDED
    RAMPING   -> HOLDING | PROMOTING | REVERTED | CONCLUDED
    HOLDING   -> RAMPING | PROMOTING | REVERTED | CONCLUDED
    PROMOTING -> PROMOTED | REVERTED
    PROMOTED  -> REVERTED            (a false-positive ship, reverted after the fact)
    REVERTED  -> (terminal)
    CONCLUDED -> (terminal)

Two invariants the property tests pin:
- REVERTED is reachable in one step from *every* non-terminal state ("any
  non-terminal can transition to REVERTED" — emergency halt must always be possible).
- REVERTED and CONCLUDED are dead ends.

Note: successive ramp steps keep an experiment in ``RAMPING`` and are *not* modelled
as a self-transition here — the orchestrator changes the allocation without a state
change, and only calls :func:`transition` when the state actually moves.
"""

from __future__ import annotations

from datatool.core.exceptions import StateTransitionError
from datatool.core.models import State

# Explicit adjacency: state -> set of states it may move to.
_LEGAL_TRANSITIONS: dict[State, frozenset[State]] = {
    State.PROPOSED: frozenset({State.CANARY, State.REVERTED, State.CONCLUDED}),
    State.CANARY: frozenset(
        {State.RAMPING, State.HOLDING, State.PROMOTING, State.REVERTED, State.CONCLUDED}
    ),
    State.RAMPING: frozenset({State.HOLDING, State.PROMOTING, State.REVERTED, State.CONCLUDED}),
    State.HOLDING: frozenset({State.RAMPING, State.PROMOTING, State.REVERTED, State.CONCLUDED}),
    State.PROMOTING: frozenset({State.PROMOTED, State.REVERTED}),
    State.PROMOTED: frozenset({State.REVERTED}),
    State.REVERTED: frozenset(),
    State.CONCLUDED: frozenset(),
}

# States the scheduler never re-evaluates (ARCHITECTURE.md §9.1). PROMOTED is
# "terminal" for the autonomous loop even though a human may still revert it.
TERMINAL_STATES: frozenset[State] = frozenset({State.PROMOTED, State.REVERTED, State.CONCLUDED})

# States with no outgoing transitions at all (true dead ends).
_DEAD_END_STATES: frozenset[State] = frozenset({State.REVERTED, State.CONCLUDED})


def legal_transitions(state: State) -> frozenset[State]:
    """Return the set of states ``state`` may legally transition to."""
    return _LEGAL_TRANSITIONS[state]


def can_transition(current: State, target: State) -> bool:
    """Whether moving from ``current`` to ``target`` is a declared legal transition."""
    return target in _LEGAL_TRANSITIONS[current]


def is_terminal_for_scheduling(state: State) -> bool:
    """Whether the scheduler should stop evaluating an experiment in this state."""
    return state in TERMINAL_STATES


def is_dead_end(state: State) -> bool:
    """Whether the state has no outgoing transitions at all."""
    return state in _DEAD_END_STATES


def transition(current: State, target: State, reason: str) -> State:
    """Validate and perform a state transition, returning the new state.

    Raises :class:`StateTransitionError` if ``current -> target`` is not legal. The
    ``reason`` is required (it becomes the ``audit_log`` entry the caller persists);
    a transition with no recorded reason is exactly the kind of unexplained state
    change the audit trail exists to prevent.
    """
    if not reason or not reason.strip():
        raise StateTransitionError(
            f"a state transition {current.value} -> {target.value} requires a reason"
        )
    if not can_transition(current, target):
        raise StateTransitionError(
            f"illegal transition {current.value} -> {target.value}; "
            f"legal targets are {sorted(s.value for s in legal_transitions(current))}"
        )
    return target
