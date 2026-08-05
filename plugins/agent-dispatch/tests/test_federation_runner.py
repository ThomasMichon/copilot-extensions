"""Tests for the federation runtime (:mod:`agent_dispatch.federation_runner`).

Drives the runner against a deterministic
:class:`~agent_dispatch.satellites.FleetDirectory` (which structurally is a
:class:`~agent_dispatch.federation.Rendezvous`) with an injected clock, plus factory
and config-wiring tests.
"""

from __future__ import annotations

import time

import pytest

from agent_dispatch import config
from agent_dispatch.federation import CoordinatorRendezvous
from agent_dispatch.federation_runner import (
    FederationRunner,
    build_rendezvous,
    gateway_rendezvous,
    local_rendezvous,
    runner_from_config,
)
from agent_dispatch.satellites import (
    ROLE_COORDINATOR,
    ROLE_STANDBY,
    FleetDirectory,
)


class FakeClock:
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
    return FleetDirectory(ttl_seconds=90.0, clock=clock)


# -- construction ------------------------------------------------------------


def test_requires_instance(directory):
    with pytest.raises(ValueError):
        FederationRunner(directory, "")


def test_rejects_unknown_role(directory):
    with pytest.raises(ValueError):
        FederationRunner(directory, "host-a", role="overlord")


@pytest.mark.parametrize(
    "role,eligible",
    [("coordinator", True), ("standby", True), ("peer", False), ("satellite", False)],
)
def test_lease_eligibility_by_role(directory, role, eligible):
    runner = FederationRunner(directory, "host-a", role=role)
    assert runner.lease_eligible is eligible


# -- presence-only roles -----------------------------------------------------


def test_peer_registers_then_heartbeats(directory):
    runner = FederationRunner(directory, "host-a", role="peer", capabilities=["logger"])
    state = runner.tick()
    assert state == {"instance": "host-a", "role": "peer", "epoch": 0, "is_active": False}
    peers = directory.discover_peers()
    assert [p["instance"] for p in peers] == ["host-a"]
    # Second tick heartbeats (no duplicate entry, still present).
    runner.tick()
    assert len(directory.discover_peers()) == 1


def test_peer_reregisters_after_reap(directory, clock):
    runner = FederationRunner(directory, "host-a", role="peer")
    runner.tick()  # registered
    clock.advance(200)  # past TTL -> reaped
    assert directory.discover_peers() == []
    runner.tick()  # heartbeat 404s -> re-register
    assert [p["instance"] for p in directory.discover_peers()] == ["host-a"]


def test_satellite_registers_with_role(directory):
    runner = FederationRunner(directory, "sat-1", role="satellite")
    runner.tick()
    sats = directory.discover_peers(role="satellite")
    assert [s["instance"] for s in sats] == ["sat-1"]


# -- lease-eligible roles ----------------------------------------------------


def test_coordinator_acquires_and_reports_active(directory):
    runner = FederationRunner(directory, "host-a", role="coordinator")
    state = runner.tick()
    assert state["is_active"] is True
    assert state["role"] == ROLE_COORDINATOR
    assert state["epoch"] == 1
    assert directory.discover_coordinator()["instance"] == "host-a"


def test_standby_reports_standby_then_fails_over(directory, clock):
    boss = FederationRunner(directory, "boss", role="coordinator", lease_ttl=30.0)
    standby = FederationRunner(directory, "host-b", role="standby", lease_ttl=30.0)
    boss.tick()  # boss active@1

    s = standby.tick()  # healthy coordinator present -> stands by
    assert s["is_active"] is False
    assert s["role"] == ROLE_STANDBY

    clock.advance(31)  # boss goes stale
    s = standby.tick()  # failover
    assert s["is_active"] is True
    assert s["role"] == ROLE_COORDINATOR
    assert s["epoch"] == 2
    assert directory.discover_coordinator()["instance"] == "host-b"


# -- reads / status ----------------------------------------------------------


def test_discover_passthrough_and_status(directory):
    coord = FederationRunner(directory, "boss", role="coordinator")
    coord.tick()
    peer = FederationRunner(directory, "host-b", role="peer")
    peer.tick()

    assert peer.discover_coordinator()["instance"] == "boss"
    assert {p["instance"] for p in peer.discover_peers()} == {"boss", "host-b"}

    st = peer.status()
    assert st["instance"] == "host-b"
    assert st["role"] == "peer"
    assert st["lease_eligible"] is False
    assert st["coordinator"]["instance"] == "boss"
    assert {p["instance"] for p in st["peers"]} == {"boss", "host-b"}


# -- resign ------------------------------------------------------------------


def test_resign_coordinator_releases_role(directory):
    runner = FederationRunner(directory, "boss", role="coordinator")
    runner.tick()
    assert directory.discover_coordinator() is not None
    runner.resign()
    assert directory.discover_coordinator() is None


def test_resign_peer_deregisters(directory):
    runner = FederationRunner(directory, "host-a", role="peer")
    runner.tick()
    assert directory.discover_peers() != []
    runner.resign()
    assert directory.discover_peers() == []


# -- background loop lifecycle -----------------------------------------------


def test_start_stop_lifecycle():
    # Real clock/threads: presence appears while running, gone after stop+resign.
    directory = FleetDirectory(ttl_seconds=90.0)
    runner = FederationRunner(directory, "host-a", role="peer")
    runner.start(interval=0.02)
    deadline = time.time() + 3
    while time.time() < deadline and not directory.discover_peers():
        time.sleep(0.02)
    assert [p["instance"] for p in directory.discover_peers()] == ["host-a"]
    runner.stop(resign=True)
    assert directory.discover_peers() == []


# -- factories ---------------------------------------------------------------


def test_build_rendezvous_returns_backend():
    rv = build_rendezvous("http://127.0.0.1:9", token="t")
    assert isinstance(rv, CoordinatorRendezvous)


def test_gateway_rendezvous_none_without_shared_url(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_SHARED_URL", raising=False)
    assert gateway_rendezvous() is None


def test_gateway_rendezvous_uses_shared_url(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_URL", "http://gw.example:9847")
    rv = gateway_rendezvous()
    assert isinstance(rv, CoordinatorRendezvous)


def test_local_rendezvous_returns_backend():
    assert isinstance(local_rendezvous("http://127.0.0.1:9"), CoordinatorRendezvous)


# -- runner_from_config ------------------------------------------------------


def test_runner_from_config_none_when_disabled(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_FEDERATION_ROLE", raising=False)
    assert runner_from_config() is None


def test_runner_from_config_builds_with_injected_rendezvous(monkeypatch, directory):
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_ROLE", "coordinator")
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_INSTANCE", "host-a")
    runner = runner_from_config(rendezvous=directory)
    assert isinstance(runner, FederationRunner)
    assert runner.instance == "host-a"
    assert runner.lease_eligible is True


def test_runner_from_config_errors_when_enabled_without_gateway(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_ROLE", "peer")
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_INSTANCE", "host-a")
    monkeypatch.delenv("AGENT_DISPATCH_SHARED_URL", raising=False)
    with pytest.raises(RuntimeError):
        runner_from_config()


# -- config readers ----------------------------------------------------------


def test_federation_role_normalizes_and_fails_closed(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_ROLE", "  Coordinator ")
    assert config.federation_role() == "coordinator"
    assert config.federation_enabled() is True
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_ROLE", "typo")
    assert config.federation_role() is None
    assert config.federation_enabled() is False


def test_federation_interval_default_and_override(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_FEDERATION_INTERVAL", raising=False)
    assert config.federation_interval() == config.DEFAULT_FEDERATION_INTERVAL
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_INTERVAL", "5")
    assert config.federation_interval() == 5.0
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_INTERVAL", "nonsense")
    assert config.federation_interval() == config.DEFAULT_FEDERATION_INTERVAL


def test_federation_instance_explicit_override(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_INSTANCE", "wheatley/wt-x")
    assert config.federation_instance() == "wheatley/wt-x"
