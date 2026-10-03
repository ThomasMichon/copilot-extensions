"""The Connection Owner never blocks its event loop on ``gh`` and never wakes a
CodeSpace GitHub has stopped (``owner_availability`` + ``session_forwards``).

Live incident: three held CodeSpaces were stopped together. The Owner rebuilt
their forwards with a synchronous ``gh codespace ssh --config`` on its event
loop, the same loop that pumps every ssh ProxyCommand. Every other CodeSpace's
relay and bridge stopped forwarding until keepalives killed them, and the
rebuild booted the stopped boxes back up.
"""

from __future__ import annotations

import asyncio
import threading
import time
import types

import pytest
from agent_codespaces import connection_owner as owner
from agent_codespaces import owner_availability as oa
from agent_codespaces import session_forwards as sf


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(owner, "OWNER_FILE", tmp_path / "connection-owner.json")
    monkeypatch.setattr(owner, "_LOCK_FILE", tmp_path / "connection-owner.lock")
    monkeypatch.setattr(owner, "LIVE_FILE", tmp_path / "connection-owner.live.json")
    monkeypatch.setattr(owner, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(owner, "ensure_runtime_dir", lambda: None)
    return tmp_path


class FakeChannel:
    def __init__(self) -> None:
        self._alive = False
        self.stops = 0

    @property
    def is_alive(self) -> bool:
        return self._alive

    async def start(self) -> None:
        self._alive = True

    async def stop(self) -> None:
        self.stops += 1
        self._alive = False


class Listing:
    """A fake ``list_codespaces`` whose states the test changes."""

    def __init__(self, **states: str) -> None:
        self.states = dict(states)
        self.calls = 0
        self.fail = False

    def __call__(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("gh codespace list failed")
        return [types.SimpleNamespace(name=n, state=s) for n, s in self.states.items()]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# -- the gate ------------------------------------------------------------------

async def test_gate_reports_only_listed_codespaces_that_are_not_available():
    gate = oa.AvailabilityGate(Listing(a="Available", b="Shutdown", c="ShuttingDown"))
    assert await gate.stopped(["a", "b", "c", "missing"]) == {"b", "c"}


async def test_a_failed_listing_never_gates_anything():
    listing = Listing(a="Shutdown")
    listing.fail = True
    gate = oa.AvailabilityGate(listing)
    assert await gate.stopped(["a"]) == set()


async def test_an_all_available_listing_is_reused_until_its_ttl():
    listing, clock = Listing(a="Available"), Clock()
    gate = oa.AvailabilityGate(listing, ttl=60.0, clock=clock)
    await gate.stopped(["a"])
    clock.now += 30.0
    await gate.stopped(["a"])
    assert listing.calls == 1
    clock.now += 31.0
    await gate.stopped(["a"])
    assert listing.calls == 2


async def test_a_down_codespace_is_relisted_every_check_so_a_restart_is_seen_at_once():
    listing, clock = Listing(a="Shutdown"), Clock()
    gate = oa.AvailabilityGate(listing, ttl=60.0, clock=clock)
    assert await gate.stopped(["a"]) == {"a"}
    listing.states["a"] = "Available"
    assert await gate.stopped(["a"]) == set()
    assert listing.calls == 2


async def test_the_listing_runs_off_the_event_loop():
    seen: list[threading.Thread] = []

    def listing():
        seen.append(threading.current_thread())
        return []

    await oa.AvailabilityGate(listing).stopped(["a"])
    assert seen and seen[0] is not threading.main_thread()


# -- the Owner -----------------------------------------------------------------

def _owner(listing: Listing, relays: dict, daemons: dict) -> owner.ConnectionOwner:
    def relay(cs):
        relays[cs] = FakeChannel()
        return relays[cs]

    def daemon(cs, port, host=None):
        daemons[(cs, port)] = FakeChannel()
        return daemons[(cs, port)]

    return owner.ConnectionOwner(
        relay,
        sessions=sf.SessionForwards(daemon, availability=oa.AvailabilityGate(listing)),
    )


async def test_owner_tears_down_and_never_rebuilds_a_stopped_codespace(store):
    owner.hold("up", "cli:a@up", daemon_port=41001, mux_session="wt-x")
    owner.hold("down", "cli:b@down", daemon_port=41002, mux_session="wt-x")
    listing, relays, daemons = Listing(up="Available", down="Available"), {}, {}
    o = _owner(listing, relays, daemons)
    await o.reconcile()
    assert o.active_codespaces() == {"up", "down"}
    first = relays["down"]

    listing.states["down"] = "Shutdown"
    first._alive = False  # stopping the box kills its forwards
    daemons[("down", 41002)]._alive = False
    await o.reconcile()
    assert o.active_codespaces() == {"up"}
    assert first.stops == 1
    assert set(o.active_daemon_forwards()) == {"up"}
    await o.reconcile()
    assert relays["down"] is first  # nothing rebuilt (a rebuild would boot it back up)

    listing.states["down"] = "Available"  # a launcher started it again
    await o.reconcile()
    assert o.active_codespaces() == {"up", "down"}
    assert relays["down"] is not first


async def test_any_lost_forward_relists_before_a_rebuild(store):
    """A reverse forward is its own ssh process: when only it dies (the box just
    stopped), the cached ``Available`` must not license its rebuild."""
    owner.hold("cs", "cli:a@cs", daemon_port=41005, mux_session="wt-x",
               reverse_forwards={9222: 7188})
    listing, daemons = Listing(cs="Available"), {}
    o = _owner(listing, {}, daemons)
    await o.reconcile()
    extra = daemons[("cs", 9222)]
    assert extra.is_alive
    calls = listing.calls
    listing.states["cs"] = "Shutdown"
    extra._alive = False  # only the reverse forward has noticed so far
    await o.reconcile()
    assert listing.calls == calls + 1  # re-listed, so the stop was seen
    assert daemons[("cs", 9222)] is extra  # never rebuilt
    assert o.active_codespaces() == set()


async def test_a_healthy_owner_reuses_its_listing(store):
    owner.hold("up", "cli:a@up", daemon_port=41004, mux_session="wt-x")
    listing = Listing(up="Available")
    o = _owner(listing, {}, {})
    await o.reconcile()  # nothing live yet: lists
    calls = listing.calls
    await o.reconcile()
    await o.reconcile()
    assert listing.calls == calls  # every forward live: the cached listing stands


async def test_without_a_gate_the_owner_keeps_every_held_codespace(store):
    owner.hold("cs", "cli:a@cs")
    created: dict = {}
    o = owner.ConnectionOwner(lambda cs: created.setdefault(cs, FakeChannel()))
    await o.reconcile()
    assert o.active_codespaces() == {"cs"}


async def test_a_slow_factory_does_not_stall_the_event_loop(store):
    owner.hold("slow", "cli:a@slow", daemon_port=41003, mux_session="wt-x")

    def slow_relay(cs):
        time.sleep(0.6)  # a gh codespace ssh --config waiting out a cold start
        return FakeChannel()

    def slow_daemon(cs, port, host=None):
        time.sleep(0.6)
        return FakeChannel()

    o = owner.ConnectionOwner(slow_relay, sessions=sf.SessionForwards(slow_daemon))
    ticks = 0

    async def pump():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.05)

    task = asyncio.create_task(pump())
    try:
        await o.reconcile()
    finally:
        task.cancel()
    # About 1.2 s of factory time ran in threads, so the pump kept running.
    assert ticks >= 10
    assert o.active_codespaces() == {"slow"}
