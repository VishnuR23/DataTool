"""Property tests for the experiment state machine (ARCHITECTURE.md §10, §17).

The state machine guarantees the controller can only move experiments along
declared paths. These tests pin: legality matches the table, illegal moves raise,
revert is always reachable from any live state, terminals are dead ends, and no
random sequence of moves can reach an undefined state. Hypothesis drives the
random sequences (§21 acceptance: 1000+ random transition sequences).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from datatool.core.exceptions import StateTransitionError
from datatool.core.models import State
from datatool.core.state_machine import (
    TERMINAL_STATES,
    can_transition,
    is_dead_end,
    legal_transitions,
    transition,
)

_STATES = list(State)
_ALL_STATES = set(_STATES)
_state = st.sampled_from(_STATES)


def test_all_legal_targets_are_known_states():
    """Every entry in the transition table points to a real State (no typos)."""
    for state in _STATES:
        for target in legal_transitions(state):
            assert isinstance(target, State)


def test_no_state_transitions_to_itself():
    """No self-loops: a state change is always to a different state.

    Successive ramps stay in RAMPING without a transition call, so a self-loop in
    the table would be dead weight that could mask a real bug.
    """
    for state in _STATES:
        assert state not in legal_transitions(state)


@given(_state, _state)
def test_transition_returns_target_iff_legal_else_raises(current, target):
    """transition() returns the target for legal moves and raises for illegal ones.

    Verifies the single enforcement point matches the legality table exactly.
    """
    if can_transition(current, target):
        assert transition(current, target, reason="test") is target
    else:
        with pytest.raises(StateTransitionError):
            transition(current, target, reason="test")


def test_revert_is_reachable_from_every_non_terminal_state():
    """REVERTED is one step away from every live state (emergency halt always works).

    Why it matters: the trust contract promises any experiment can be halted; if a
    state could not reach REVERTED, a misbehaving experiment could get stuck live.
    """
    for state in _STATES:
        if is_dead_end(state):
            continue
        assert State.REVERTED in legal_transitions(state)


def test_dead_end_states_have_no_outgoing_transitions():
    """REVERTED and CONCLUDED are terminal — no further moves are legal."""
    assert legal_transitions(State.REVERTED) == frozenset()
    assert legal_transitions(State.CONCLUDED) == frozenset()
    for target in _STATES:
        assert not can_transition(State.REVERTED, target)
        assert not can_transition(State.CONCLUDED, target)


def test_scheduler_terminal_set_matches_spec():
    """The scheduler-terminal set is exactly {PROMOTED, REVERTED, CONCLUDED} (§9.1)."""
    assert TERMINAL_STATES == frozenset({State.PROMOTED, State.REVERTED, State.CONCLUDED})


def test_transition_requires_a_nonempty_reason():
    """A transition with no reason raises — every state change must be explainable.

    The reason becomes the audit-log entry; an unexplained transition defeats the
    audit trail the project depends on.
    """
    with pytest.raises(StateTransitionError):
        transition(State.PROPOSED, State.CANARY, reason="")
    with pytest.raises(StateTransitionError):
        transition(State.PROPOSED, State.CANARY, reason="   ")


def test_every_state_is_reachable_from_proposed():
    """No unreachable states: BFS from PROPOSED visits all eight states.

    An unreachable state would be dead code the controller could never produce —
    or worse, a state it produces by an undeclared path.
    """
    seen = {State.PROPOSED}
    frontier = [State.PROPOSED]
    while frontier:
        node = frontier.pop()
        for nxt in legal_transitions(node):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    assert seen == set(_STATES)


@settings(max_examples=500)
@given(st.lists(_state, min_size=1, max_size=40))
def test_random_transition_sequence_never_reaches_an_undefined_state(targets):
    """Walking a random sequence of target picks never yields an undefined state.

    Legal picks advance the state and are returned verbatim; illegal picks raise
    and leave the state unchanged. Across hundreds of sequences this asserts the
    machine never silently lands somewhere undeclared.
    """
    state = State.PROPOSED
    for target in targets:
        if can_transition(state, target):
            state = transition(state, target, reason="walk")
            assert state is target
        else:
            with pytest.raises(StateTransitionError):
                transition(state, target, reason="walk")
        assert state in _ALL_STATES
