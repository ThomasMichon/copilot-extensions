"""Structural tests for the three deferred simulation-scenario declarations:
bridge liveness-recovery-with-port-rediscovery, EOL retire safety, and the
steer-vs-transition dual-outcome contract.

These assert the properties
``efforts/active/review-automation-reliability/phase-9-state-machine-architecture.md``
requires of each: the correct terminal outcome is reached regardless of how
many transient blips occur before it, the recovery falls back to COLD
rather than ever assuming HOT, EOL safety is true only at zero leases, and
the steer outcome maps to a real declared task transition. No process, no
live bridge or supervisor -- deterministic, in-process fixtures only.
"""

from __future__ import annotations

import pytest

from agent_dispatch.bridge_state_machine import (
    Liveness,
    eol_safe_to_retire,
    resolve_liveness_with_recovery,
)
from agent_dispatch.task_state_machine import (
    STEER_OUTCOME_TRANSITION,
    TRANSITIONS,
    SteerOutcome,
    resolve_steer_outcome,
    suspend_blocked_by_pending_steer,
)

TASK_TRANSITION_NAMES = frozenset(t.name for t in TRANSITIONS)


# --- Bridge mid-version-update: bounded-retry liveness recovery -------------


def test_recovery_succeeds_on_first_attempt_without_retry():
    ports_discovered = []

    def discover_port():
        ports_discovered.append("port-a")
        return "port-a"

    def probe(port):
        assert port == "port-a"
        return Liveness.WARM

    result = resolve_liveness_with_recovery(None, probe, discover_port)
    assert result is Liveness.WARM
    assert ports_discovered == ["port-a"]  # exactly one discovery


def test_recovery_rediscovers_port_after_a_transient_blip():
    """A stale-port-then-fresh-port sequence: the first probe raises (the
    port moved underneath it), a fresh discover_port() call gets the new
    port, and the second probe succeeds."""
    ports = iter(["stale-port", "fresh-port"])
    discovered = []

    def discover_port():
        port = next(ports)
        discovered.append(port)
        return port

    calls = []

    def probe(port):
        calls.append(port)
        if port == "stale-port":
            raise ConnectionError("blip")
        return Liveness.WARM

    result = resolve_liveness_with_recovery(None, probe, discover_port, max_attempts=3)
    assert result is Liveness.WARM
    assert discovered == ["stale-port", "fresh-port"]
    assert calls == ["stale-port", "fresh-port"]


def test_recovery_falls_back_to_cold_once_attempts_exhaust_never_hot():
    """Every attempt blips; recovery must fall back to COLD, never assume
    HOT, once max_attempts is exhausted."""

    def discover_port():
        return "always-stale"

    def probe(port):
        raise ConnectionError("still blipping")

    result = resolve_liveness_with_recovery(None, probe, discover_port, max_attempts=3)
    assert result is Liveness.COLD


def test_recovery_ignores_the_cache_hint_value_like_resolve_liveness():
    def discover_port():
        return "port"

    def probe(port):
        return Liveness.HOT

    result = resolve_liveness_with_recovery(Liveness.COLD, probe, discover_port)
    assert result is Liveness.HOT


def test_recovery_never_calls_discover_port_internally_from_probe():
    """discover_port is injected, not called by live_probe -- a fixture
    must be able to drive the sequence deterministically from the outside."""
    discover_calls = []

    def discover_port():
        discover_calls.append(1)
        return "p"

    def probe(port):
        return Liveness.WARM

    resolve_liveness_with_recovery(None, probe, discover_port)
    # Exactly one discovery for a first-attempt success -- no hidden extra
    # calls sneaking in from inside probe.
    assert len(discover_calls) == 1


# --- EOL: bridge/runtime retirement safety ----------------------------------


def test_eol_safe_only_at_zero_active_leases():
    assert eol_safe_to_retire(0) is True


@pytest.mark.parametrize("count", [1, 2, 100])
def test_eol_unsafe_with_any_active_lease(count):
    assert eol_safe_to_retire(count) is False


def test_eol_predicate_never_negative_is_still_well_defined():
    """A defensive case: eol_safe_to_retire is a pure predicate over an
    int and must not special-case an impossible negative count."""
    assert eol_safe_to_retire(-1) is False


# --- Steer-vs-transition race: dual-outcome contract ------------------------


def test_interactive_owner_resumes_with_wake():
    assert resolve_steer_outcome(is_headless_reservation=False) is SteerOutcome.RESUME_WITH_WAKE


def test_headless_reservation_releases_to_queued():
    assert resolve_steer_outcome(is_headless_reservation=True) is SteerOutcome.RELEASE_TO_QUEUED


@pytest.mark.parametrize("outcome", list(SteerOutcome))
def test_every_steer_outcome_names_a_real_task_transition(outcome):
    transition_name = STEER_OUTCOME_TRANSITION[outcome]
    assert transition_name in TASK_TRANSITION_NAMES


def test_suspend_is_blocked_while_a_steer_answer_is_untaken():
    assert suspend_blocked_by_pending_steer(has_untaken_steer=True) is True


def test_suspend_is_not_blocked_once_the_steer_answer_is_taken():
    assert suspend_blocked_by_pending_steer(has_untaken_steer=False) is False
