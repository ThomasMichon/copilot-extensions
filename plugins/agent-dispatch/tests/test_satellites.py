"""Tests for the in-memory satellite presence registry."""

from __future__ import annotations

import pytest

from agent_dispatch.satellites import (
    DEFAULT_TTL_SECONDS,
    SatelliteRegistry,
    UnknownSatellite,
)


class FakeClock:
    """Manually-advanced clock for deterministic TTL tests."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = float(t)

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += float(dt)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def reg(clock):
    return SatelliteRegistry(ttl_seconds=90.0, clock=clock)


# -- register ----------------------------------------------------------------


def test_register_returns_live_entry(reg, clock):
    e = reg.register(
        "field-laptop",
        worktrees=["wt-a", "wt-b"],
        capabilities=["logger"],
        gate_state="open",
        agent_versions={"agent-dispatch": "0.1.0-dev104"},
    )
    assert e["machine"] == "field-laptop"
    assert e["worktrees"] == ["wt-a", "wt-b"]
    assert e["capabilities"] == ["logger"]
    assert e["gate_state"] == "open"
    assert e["agent_versions"] == {"agent-dispatch": "0.1.0-dev104"}
    assert e["last_seen"] == clock.t
    assert e["expires_at"] == clock.t + 90.0
    assert e["age"] == 0.0


def test_register_requires_machine(reg):
    with pytest.raises(ValueError):
        reg.register("")


def test_register_is_idempotent_and_preserves_registered_at(reg, clock):
    first = reg.register("book2", worktrees=["wt-a"])
    clock.advance(10)
    second = reg.register("book2", worktrees=["wt-a", "wt-b"], gate_state="closed")
    # Re-register refreshes fields + last_seen but keeps the original registered_at.
    assert second["registered_at"] == first["registered_at"]
    assert second["last_seen"] == clock.t
    assert second["worktrees"] == ["wt-a", "wt-b"]
    assert second["gate_state"] == "closed"
    assert len(reg.list()) == 1


def test_default_ttl_constant():
    r = SatelliteRegistry()
    assert r.ttl_seconds == DEFAULT_TTL_SECONDS


# -- heartbeat ---------------------------------------------------------------


def test_heartbeat_refreshes_last_seen_and_status(reg, clock):
    reg.register("book2", worktrees=["wt-a"])
    clock.advance(30)
    e = reg.heartbeat("book2", status={"wt-a": {"turn_state": "active"}})
    assert e["last_seen"] == clock.t
    assert e["status"] == {"wt-a": {"turn_state": "active"}}
    assert e["age"] == 0.0


def test_heartbeat_can_update_worktrees_and_gate(reg, clock):
    reg.register("book2", worktrees=["wt-a"], gate_state="open")
    e = reg.heartbeat("book2", worktrees=["wt-a", "wt-c"], gate_state="closed")
    assert e["worktrees"] == ["wt-a", "wt-c"]
    assert e["gate_state"] == "closed"


def test_heartbeat_unknown_raises(reg):
    with pytest.raises(UnknownSatellite):
        reg.heartbeat("never-registered")


def test_heartbeat_after_expiry_raises_and_drops(reg, clock):
    reg.register("book2")
    clock.advance(91)  # past the 90s TTL
    with pytest.raises(UnknownSatellite):
        reg.heartbeat("book2")
    # The stale record is dropped, not resurrected.
    assert reg.get("book2") is None
    assert reg.list() == []


def test_heartbeat_keeps_alive_indefinitely(reg, clock):
    reg.register("book2")
    for _ in range(10):
        clock.advance(80)  # inside the 90s TTL each time
        reg.heartbeat("book2")
    assert reg.is_registered("book2")


# -- expiry / reaping --------------------------------------------------------


def test_get_returns_none_after_expiry(reg, clock):
    reg.register("book2")
    assert reg.get("book2") is not None
    clock.advance(90.1)
    assert reg.get("book2") is None


def test_list_reaps_expired(reg, clock):
    reg.register("a")
    clock.advance(50)
    reg.register("b")
    clock.advance(50)  # a is now 100s old (dead), b is 50s old (live)
    live = reg.list()
    assert [e["machine"] for e in live] == ["b"]


def test_list_is_sorted_by_machine(reg):
    reg.register("charlie")
    reg.register("alpha")
    reg.register("bravo")
    assert [e["machine"] for e in reg.list()] == ["alpha", "bravo", "charlie"]


def test_reap_returns_count(reg, clock):
    reg.register("a")
    reg.register("b")
    clock.advance(91)
    reg.register("c")  # fresh
    assert reg.reap() == 2
    assert [e["machine"] for e in reg.list()] == ["c"]


# -- deregister --------------------------------------------------------------


def test_deregister_removes_entry(reg):
    reg.register("book2")
    assert reg.deregister("book2") is True
    assert reg.get("book2") is None


def test_deregister_absent_returns_false(reg):
    assert reg.deregister("nope") is False


# -- is_registered (the overlay predicate) -----------------------------------


def test_is_registered_reflects_liveness(reg, clock):
    assert reg.is_registered("book2") is False
    reg.register("book2")
    assert reg.is_registered("book2") is True
    clock.advance(90.1)
    assert reg.is_registered("book2") is False


def test_status_is_copied_not_aliased(reg):
    pushed = {"wt-a": {"turn_state": "active"}}
    reg.register("book2", status=pushed)
    pushed["wt-a"]["turn_state"] = "idle"  # mutate caller's dict
    assert reg.get("book2")["status"] == {"wt-a": {"turn_state": "active"}}
