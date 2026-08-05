"""Tests for the fenced-epoch coordinator lease + fencing guard
(:mod:`agent_dispatch.lease`).

The substrate is a :class:`~agent_dispatch.satellites.FleetDirectory` driven by an
injected clock: it structurally *is* a
:class:`~agent_dispatch.federation.Rendezvous`, so the lease drives it directly
with no HTTP, and advancing the fake clock deterministically ages a coordinator
entry past the lease's staleness threshold. One integration test drives the lease
over the real HTTP-backed :class:`CoordinatorRendezvous`.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.federation import CoordinatorRendezvous, Rendezvous
from agent_dispatch.lease import (
    DEFAULT_LEASE_TTL_SECONDS,
    CoordinatorLease,
    FencedError,
    FencingGuard,
    LeaseState,
    current_fencing_epoch,
)
from agent_dispatch.satellites import (
    ROLE_COORDINATOR,
    ROLE_STANDBY,
    FleetDirectory,
    UnknownInstance,
)
from tests._helpers import RepoDefaultingQueue as TaskQueue


class FakeClock:
    """A manually advanced monotonic clock."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += float(seconds)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def directory(clock: FakeClock) -> FleetDirectory:
    # TTL comfortably larger than the lease staleness threshold, so a coordinator
    # can be "stale" (failover-eligible) while still present (not yet reaped).
    return FleetDirectory(ttl_seconds=90.0, clock=clock)


# -- the substrate is a real Rendezvous --------------------------------------


def test_fleet_directory_satisfies_rendezvous(directory):
    assert isinstance(directory, Rendezvous)


# -- acquire / renew ---------------------------------------------------------


def test_first_tick_acquires_epoch_one(directory):
    lease = CoordinatorLease(directory, "host-a")
    state = lease.tick()
    assert state == LeaseState("host-a", 1, ROLE_COORDINATOR, True)
    assert lease.is_active is True
    assert lease.epoch == 1
    coord = directory.discover_coordinator()
    assert coord["instance"] == "host-a"
    assert coord["epoch"] == 1
    assert coord["role"] == ROLE_COORDINATOR


def test_renew_keeps_same_epoch(directory, clock):
    lease = CoordinatorLease(directory, "host-a")
    lease.tick()
    clock.advance(5)
    state = lease.tick()
    assert state.is_active is True
    assert state.epoch == 1  # renew does not bump the epoch
    assert lease.epoch == 1


def test_requires_instance(directory):
    with pytest.raises(ValueError):
        CoordinatorLease(directory, "")


# -- standby -----------------------------------------------------------------


def test_stands_by_behind_a_healthy_coordinator(directory):
    directory.register("boss", role=ROLE_COORDINATOR, epoch=5)
    lease = CoordinatorLease(directory, "host-b")
    state = lease.tick()
    assert state.is_active is False
    assert state.role == ROLE_STANDBY
    assert lease.epoch == 0
    # A never-promoted standby does not create its own directory entry.
    assert [e["instance"] for e in directory.discover_peers()] == ["boss"]
    # The healthy coordinator is untouched.
    assert directory.discover_coordinator()["instance"] == "boss"


# -- failover ----------------------------------------------------------------


def test_standby_takes_over_stale_coordinator_with_next_epoch(directory, clock):
    directory.register("boss", role=ROLE_COORDINATOR, epoch=5)
    lease = CoordinatorLease(directory, "host-b", lease_ttl=30.0)

    # Fresh -> stands by.
    assert lease.tick().is_active is False

    # Age the coordinator past the staleness threshold (but under the 90s TTL, so
    # it is stale-but-present) -> failover with the next epoch, fencing the deposed.
    clock.advance(31)
    state = lease.tick()
    assert state.is_active is True
    assert state.epoch == 6  # 5 + 1
    coord = directory.discover_coordinator()
    assert coord["instance"] == "host-b"
    assert coord["epoch"] == 6


def test_takeover_when_coordinator_reaped(directory, clock):
    directory.register("boss", role=ROLE_COORDINATOR, epoch=5)
    lease = CoordinatorLease(directory, "host-b")
    lease.tick()  # observes boss@5, stands by

    # Past the directory TTL -> boss is reaped, discover returns None. The lease
    # still takes over strictly above the highest epoch it ever observed.
    clock.advance(200)
    state = lease.tick()
    assert state.is_active is True
    assert state.epoch == 6
    assert directory.discover_coordinator()["instance"] == "host-b"


def test_deposed_coordinator_steps_down_on_next_tick(directory, clock):
    boss = CoordinatorLease(directory, "boss", lease_ttl=30.0)
    standby = CoordinatorLease(directory, "host-b", lease_ttl=30.0)

    boss.tick()  # boss@1 active
    standby.tick()  # stands by

    # boss stops heartbeating; standby fails it over.
    clock.advance(31)
    assert standby.tick().epoch == 2

    # boss comes back and ticks: it now sees host-b@2 fresh -> steps down.
    state = boss.tick()
    assert state.is_active is False
    assert state.role == ROLE_STANDBY
    assert boss.is_active is False
    # The directory advertises exactly one coordinator (host-b); boss downgraded.
    assert directory.discover_coordinator()["instance"] == "host-b"
    coords = directory.discover_peers(role=ROLE_COORDINATOR)
    assert [c["instance"] for c in coords] == ["host-b"]


def test_epoch_is_monotonic_across_repeated_failovers(directory, clock):
    a = CoordinatorLease(directory, "a", lease_ttl=30.0)
    b = CoordinatorLease(directory, "b", lease_ttl=30.0)
    a.tick()  # a@1
    clock.advance(31)
    assert b.tick().epoch == 2  # b takes over -> 2
    clock.advance(31)
    assert a.tick().epoch == 3  # a takes back -> 3 (strictly increasing)


# -- resign ------------------------------------------------------------------


def test_resign_lets_a_standby_take_over_immediately(directory):
    boss = CoordinatorLease(directory, "boss")
    standby = CoordinatorLease(directory, "host-b")
    boss.tick()  # boss@1
    standby.tick()  # observes boss@1, stands by

    boss.resign()
    assert boss.is_active is False
    assert directory.discover_coordinator() is None

    # No wait needed: the entry is gone, so the standby takes over at once.
    state = standby.tick()
    assert state.is_active is True
    assert state.epoch == 2  # above the observed boss@1


# -- fencing guard -----------------------------------------------------------


def test_current_fencing_epoch(directory):
    assert current_fencing_epoch(directory) == 0
    CoordinatorLease(directory, "host-a").tick()
    assert current_fencing_epoch(directory) == 1


def test_guard_rejects_deposed_writer_after_failover(directory, clock):
    boss = CoordinatorLease(directory, "boss", lease_ttl=30.0)
    standby = CoordinatorLease(directory, "host-b", lease_ttl=30.0)
    guard = FencingGuard(directory)

    boss.tick()  # boss@1
    old_token = boss.fencing_token()
    assert guard.is_valid(old_token) is True
    guard.check(old_token)  # valid now

    clock.advance(31)
    standby.tick()  # host-b@2 takes over

    # The deposed boss's late directive (token @1) is fenced; the new writer's is not.
    assert guard.current_epoch() == 2
    assert guard.is_valid(old_token) is False
    with pytest.raises(FencedError):
        guard.check(old_token)
    guard.check(standby.fencing_token())  # @2 -> valid


# -- races and edge branches -------------------------------------------------


class RacingDirectory(FleetDirectory):
    """Injects a higher-id competing coordinator right before the take-over
    confirm-read, to exercise the lost-tie stand-down path deterministically."""

    def __init__(self, *args, competitor: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._competitor = competitor
        self._calls = 0

    def discover_coordinator(self):
        self._calls += 1
        if self._calls == 2 and self._competitor:  # the confirm-read of one tick
            coords = super().discover_peers(role=ROLE_COORDINATOR)
            epoch = max((c["epoch"] for c in coords), default=1)
            super().register(self._competitor, role=ROLE_COORDINATOR, epoch=epoch)
            self._competitor = None
        return super().discover_coordinator()


def test_simultaneous_takeover_resolves_to_single_winner(clock):
    # "host-a" loses the tie to the higher-id "host-z" injected at the same epoch.
    directory = RacingDirectory(ttl_seconds=90.0, clock=clock, competitor="host-z")
    lease = CoordinatorLease(directory, "host-a")

    state = lease.tick()
    assert state.is_active is False  # lost the tie -> stood down
    assert state.role == ROLE_STANDBY

    coord = directory.discover_coordinator()
    assert coord["instance"] == "host-z"
    # Exactly one coordinator advertised: host-a downgraded its own entry.
    coords = directory.discover_peers(role=ROLE_COORDINATOR)
    assert [c["instance"] for c in coords] == ["host-z"]


class HeartbeatReaps(FleetDirectory):
    """A directory whose next heartbeat 404s once (a tight reap race), so the
    lease must re-assert via register at the same epoch."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.raise_once = False

    def heartbeat(self, instance, **kwargs):
        if self.raise_once:
            self.raise_once = False
            raise UnknownInstance(instance)
        return super().heartbeat(instance, **kwargs)


def test_renew_self_heals_after_reap_race(clock):
    directory = HeartbeatReaps(ttl_seconds=90.0, clock=clock)
    lease = CoordinatorLease(directory, "host-a")
    lease.tick()  # host-a@1 active (via register)

    directory.raise_once = True
    state = lease.tick()  # discover says us -> renew -> heartbeat 404s -> re-register
    assert state.is_active is True
    assert state.epoch == 1  # re-asserted at the same epoch, not bumped
    assert directory.discover_coordinator()["instance"] == "host-a"


def test_lease_ttl_default_is_shorter_than_directory_ttl():
    from agent_dispatch.satellites import DEFAULT_TTL_SECONDS

    assert DEFAULT_LEASE_TTL_SECONDS < DEFAULT_TTL_SECONDS


# -- integration: the lease over the real HTTP-backed rendezvous -------------


@pytest.fixture
def http_rv(tmp_path):
    """A CoordinatorRendezvous wired to a real coordinator on an ephemeral port."""
    import uvicorn

    app = create_app(TaskQueue(tmp_path / "tasks.db"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    client = DispatchClient(f"http://127.0.0.1:{port}")
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            client.health()
            break
        except Exception:
            time.sleep(0.05)
    else:
        client.close()
        raise RuntimeError("coordinator did not start")

    yield CoordinatorRendezvous(client)

    client.close()
    server.should_exit = True
    thread.join(timeout=5)


def test_lease_and_guard_over_http(http_rv):
    lease = CoordinatorLease(http_rv, "host-a", machine="book2")
    state = lease.tick()
    assert state.is_active is True
    assert state.epoch == 1

    # Discoverable over HTTP; the guard fences a stale token and passes the live one.
    coord = http_rv.discover_coordinator()
    assert coord["instance"] == "host-a"
    guard = FencingGuard(http_rv)
    assert guard.current_epoch() == 1
    with pytest.raises(FencedError):
        guard.check(0)
    guard.check(lease.fencing_token())
