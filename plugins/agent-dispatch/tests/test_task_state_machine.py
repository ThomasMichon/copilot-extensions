"""Structural tests for the declared task state-machine table.

These are Phase 9 (``review-automation-reliability`` effort) fixtures: they
assert properties of the *declared* transition table in
``agent_dispatch.task_state_machine``, not a live queue/coordinator. No
process, no filesystem, no network -- purely a check that the table itself
is internally consistent before any scenario fixture is built on top of it.
"""

from __future__ import annotations

import pytest

from agent_dispatch.queue import Status
from agent_dispatch.task_state_machine import (
    ALL_STATES,
    INITIAL_STATE,
    TERMINAL_STATES,
    TRANSITIONS,
    RecoveryMode,
    reachable_states,
    states_without_exit,
    terminal_states_with_exit,
)


def test_all_states_match_queue_status():
    """The declared table's state set is exactly ``Status``'s eight states."""
    assert ALL_STATES == {
        Status.PROPOSED,
        Status.QUEUED,
        Status.CLAIMED,
        Status.STARTED,
        Status.SUSPENDED,
        Status.COMPLETED,
        Status.ABANDONED,
        Status.DEAD_LETTER,
    }


def test_terminal_states_match_queue_status():
    assert TERMINAL_STATES == Status.TERMINAL


def test_every_state_reachable_from_initial():
    reachable = reachable_states(INITIAL_STATE)
    unreachable = ALL_STATES - reachable
    assert not unreachable, f"unreachable from {INITIAL_STATE!r}: {unreachable}"


def test_no_nonterminal_state_lacks_an_exit():
    dead_ends = states_without_exit()
    assert not dead_ends, f"non-terminal states with no declared exit: {dead_ends}"


def test_no_terminal_state_has_a_declared_exit():
    leaky = terminal_states_with_exit()
    assert not leaky, f"terminal states with a declared exit: {leaky}"


def test_every_transition_names_only_declared_states():
    for transition in TRANSITIONS:
        assert transition.from_states <= ALL_STATES, transition.name
        assert transition.to_state in ALL_STATES, transition.name


def test_every_transition_has_exactly_one_recovery_mode():
    for transition in TRANSITIONS:
        assert isinstance(transition.recovery_mode, RecoveryMode), transition.name


def test_no_transition_originates_from_a_terminal_state():
    """A terminal state must never be a legal transition source."""
    for transition in TRANSITIONS:
        overlap = transition.from_states & TERMINAL_STATES
        assert not overlap, f"{transition.name} sources from terminal {overlap}"


def test_transition_names_are_unique():
    names = [t.name for t in TRANSITIONS]
    assert len(names) == len(set(names)), names


@pytest.mark.parametrize(
    "held_transition_name",
    ["requeue_held", "dead_letter_held"],
)
def test_held_transitions_match_queue_held_definition(held_transition_name):
    """``Status.HELD`` transitions must track the real HELD set, not a copy."""
    (transition,) = [t for t in TRANSITIONS if t.name == held_transition_name]
    assert transition.from_states == Status.HELD


def test_abandon_matches_queue_abandonable_definition():
    (transition,) = [t for t in TRANSITIONS if t.name == "abandon"]
    assert transition.from_states == Status.ABANDONABLE


def test_recovery_modes_are_not_all_the_same():
    """A table where every transition got the same tag is a design smell --
    the taxonomy exists to distinguish real cases, not to be applied
    mechanically."""
    modes = {t.recovery_mode for t in TRANSITIONS}
    assert len(modes) > 1
