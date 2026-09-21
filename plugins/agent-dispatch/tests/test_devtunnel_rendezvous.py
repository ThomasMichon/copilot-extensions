"""Tests for the Dev Tunnels federation rendezvous backend
(:mod:`agent_dispatch.devtunnel_rendezvous`), Phase 4 of the
``agent-dispatch-federation`` effort.

Drives :class:`DevTunnelRendezvous` against a fake in-process ``devtunnel`` CLI
(an injected ``runner`` callable) that mimics the real subcommands' JSON
output closely enough to exercise the full register/heartbeat/deregister/
discover_* logic without a real Dev Tunnels account.
"""

from __future__ import annotations

import json
import subprocess
import time

import pytest

from agent_dispatch import config
from agent_dispatch.devtunnel_rendezvous import (
    DevTunnelError,
    DevTunnelRendezvous,
    _tunnel_id,
    devtunnel_rendezvous,
)
from agent_dispatch.federation import Rendezvous
from agent_dispatch.satellites import UnknownInstance


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += float(seconds)


class FakeDevTunnelCLI:
    """An in-memory stand-in for the ``devtunnel`` CLI's JSON-mode subcommands.

    Stores tunnels keyed by id; ``__call__`` mimics argv parsing closely enough
    to drive :class:`DevTunnelRendezvous` end to end.
    """

    def __init__(self) -> None:
        self._tunnels: dict[str, dict] = {}

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        assert args[-1] == "--json"
        cmd, rest = args[0], args[1:-1]
        if cmd == "show":
            tunnel_id = rest[0]
            tunnel = self._tunnels.get(tunnel_id)
            if tunnel is None:
                return self._result(1, stderr=f"Tunnel {tunnel_id} not found")
            return self._result(0, stdout=json.dumps({"tunnel": tunnel}))
        if cmd == "create":
            tunnel_id, opts = rest[0], _parse_opts(rest[1:])
            if tunnel_id in self._tunnels:
                return self._result(1, stderr="Conflict with existing entity.")
            tunnel = {
                "tunnelId": tunnel_id,
                "labels": opts.get("labels", "").split(),
                "description": opts.get("description", ""),
                "tunnelExpiration": opts.get("expiration", ""),
            }
            self._tunnels[tunnel_id] = tunnel
            return self._result(0, stdout=json.dumps({"tunnel": tunnel}))
        if cmd == "update":
            tunnel_id, opts = rest[0], _parse_opts(rest[1:])
            tunnel = self._tunnels.get(tunnel_id)
            if tunnel is None:
                return self._result(1, stderr=f"Tunnel {tunnel_id} not found")
            if "description" in opts:
                tunnel["description"] = opts["description"]
            if "expiration" in opts:
                tunnel["tunnelExpiration"] = opts["expiration"]
            return self._result(0, stdout=json.dumps({"tunnel": tunnel}))
        if cmd == "delete":
            tunnel_id = rest[0]
            self._tunnels.pop(tunnel_id, None)
            return self._result(0, stdout=json.dumps({"deletedTunnel": tunnel_id}))
        if cmd == "list":
            opts = _parse_opts(rest)
            label = opts.get("all-labels")
            tunnels = [
                t
                for t in self._tunnels.values()
                if label is None or label in t.get("labels", [])
            ]
            return self._result(0, stdout=json.dumps({"tunnels": tunnels}))
        raise AssertionError(f"unhandled fake devtunnel command: {args}")

    def _result(
        self, code: int, *, stdout: str = "", stderr: str = ""
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["devtunnel"], code, stdout, stderr)


def _parse_opts(tokens: list[str]) -> dict[str, str]:
    opts: dict[str, str] = {}
    i = 0
    flags = {
        "--labels": "labels",
        "--description": "description",
        "--expiration": "expiration",
        "--all-labels": "all-labels",
    }
    while i < len(tokens):
        key = flags.get(tokens[i])
        if key is not None and i + 1 < len(tokens):
            opts[key] = tokens[i + 1]
            i += 2
        else:
            i += 1
    return opts


@pytest.fixture
def cli() -> FakeDevTunnelCLI:
    return FakeDevTunnelCLI()


@pytest.fixture
def rv(cli: FakeDevTunnelCLI) -> DevTunnelRendezvous:
    return DevTunnelRendezvous(runner=cli, ttl_seconds=90.0)


# -- Protocol conformance -----------------------------------------------------


def test_backend_satisfies_the_protocol(rv):
    assert isinstance(rv, Rendezvous)


# -- tunnel id mapping --------------------------------------------------------


def test_tunnel_id_is_deterministic_and_safe():
    assert _tunnel_id("lambda-core") == _tunnel_id("lambda-core")
    assert _tunnel_id("wt/sweep").startswith("adf-")
    assert " " not in _tunnel_id("a b/c")


def test_tunnel_id_does_not_collide_after_sanitization():
    # "a/b" and "a-b" would sanitize to the same prefix; the digest suffix
    # must still distinguish them.
    assert _tunnel_id("a/b") != _tunnel_id("a-b")


def test_tunnel_id_does_not_collide_after_truncation():
    long_a = "x" * 80 + "-instance-one"
    long_b = "x" * 80 + "-instance-two"
    assert _tunnel_id(long_a) != _tunnel_id(long_b)


# -- awareness plane ----------------------------------------------------------


def test_register_then_discover_peers(rv):
    entry = rv.register("host-a", capabilities=["logger"])
    assert entry["instance"] == "host-a"
    assert entry["role"] == "peer"
    peers = rv.discover_peers()
    assert [p["instance"] for p in peers] == ["host-a"]


def test_discover_peers_filters_by_role(rv):
    rv.register("peer-1", role="peer")
    rv.register("sat-1", role="satellite")
    assert [p["instance"] for p in rv.discover_peers(role="satellite")] == ["sat-1"]


def test_register_is_idempotent_and_preserves_registered_at(rv):
    first = rv.register("host-a")
    second = rv.register("host-a", role="standby")
    assert second["registered_at"] == first["registered_at"]
    assert second["role"] == "standby"


def test_heartbeat_keeps_entry_and_bumps_last_seen(rv):
    rv.register("host-a")
    first_seen = rv.discover_peers()[0]["last_seen"]
    time.sleep(0.01)
    entry = rv.heartbeat("host-a")
    assert entry["last_seen"] >= first_seen


def test_heartbeat_unknown_raises(rv):
    with pytest.raises(UnknownInstance):
        rv.heartbeat("never-registered")


def test_deregister_returns_bool(rv):
    rv.register("host-a")
    assert rv.deregister("host-a") is True
    assert rv.deregister("host-a") is False
    assert rv.discover_peers() == []


def test_register_carries_capabilities_worktrees_status(rv):
    entry = rv.register(
        "host-a",
        capabilities=["logger"],
        worktrees=["wt-1"],
        agent_versions={"agent-dispatch": "0.1.2"},
        status={"turn_state": "active"},
    )
    assert entry["capabilities"] == ["logger"]
    assert entry["worktrees"] == ["wt-1"]
    assert entry["agent_versions"] == {"agent-dispatch": "0.1.2"}
    assert entry["status"] == {"turn_state": "active"}


def test_heartbeat_preserves_capabilities_from_register(rv):
    rv.register("host-a", capabilities=["logger"], agent_versions={"a": "1"})
    entry = rv.heartbeat("host-a")
    assert entry["capabilities"] == ["logger"]
    assert entry["agent_versions"] == {"a": "1"}


def test_upsert_refuses_to_overwrite_foreign_tunnel(cli):
    # A tunnel that happens to share the deterministic id but was not created
    # under this backend's label must never be silently overwritten.
    tunnel_id = _tunnel_id("host-a")
    cli._tunnels[tunnel_id] = {
        "tunnelId": tunnel_id,
        "labels": ["some-other-tool"],
        "description": "",
    }
    rv = DevTunnelRendezvous(runner=cli)
    with pytest.raises(DevTunnelError):
        rv.register("host-a")


def test_deregister_refuses_to_delete_foreign_tunnel(cli):
    tunnel_id = _tunnel_id("host-a")
    cli._tunnels[tunnel_id] = {
        "tunnelId": tunnel_id,
        "labels": ["some-other-tool"],
        "description": "",
    }
    rv = DevTunnelRendezvous(runner=cli)
    with pytest.raises(DevTunnelError):
        rv.deregister("host-a")
    # The foreign tunnel must still be there afterward.
    assert tunnel_id in cli._tunnels


# -- claim plane ---------------------------------------------------------------


def test_discover_coordinator_none_when_absent(rv):
    rv.register("peer-1", role="peer")
    assert rv.discover_coordinator() is None


def test_discover_coordinator_picks_highest_epoch(rv):
    rv.register("c-old", role="coordinator", epoch=2)
    rv.register("c-new", role="coordinator", epoch=5)
    coord = rv.discover_coordinator()
    assert coord["instance"] == "c-new"
    assert coord["epoch"] == 5


def test_register_carries_epoch_and_distinct_machine(rv):
    entry = rv.register(
        "mantis-counter-wt-sweep", role="coordinator", epoch=4, machine="mantis-counter"
    )
    assert entry["epoch"] == 4
    assert entry["machine"] == "mantis-counter"


# -- TTL / liveness --------------------------------------------------------------


def test_stale_entry_excluded_from_discovery(cli):
    rv = DevTunnelRendezvous(runner=cli, ttl_seconds=0.05)
    rv.register("host-a")
    time.sleep(0.1)
    assert rv.discover_peers() == []


def test_reap_stale_deletes_dead_tunnels(cli):
    rv = DevTunnelRendezvous(runner=cli, ttl_seconds=0.05)
    rv.register("host-a")
    time.sleep(0.1)
    assert rv.reap_stale() == 1
    assert cli._tunnels == {}


# -- malformed payload robustness -------------------------------------------------


def test_discover_peers_skips_malformed_description(cli, rv):
    rv.register("host-a")
    # Hand-craft a second, foreign-looking tunnel carrying the same label but a
    # payload whose fields have the wrong types -- must be skipped, not raise.
    cli._tunnels["adf-bogus"] = {
        "tunnelId": "adf-bogus",
        "labels": [cli._tunnels[_tunnel_id("host-a")]["labels"][0]],
        "description": json.dumps({"instance": "bogus", "last_seen": "not-a-number"}),
    }
    peers = rv.discover_peers()
    assert [p["instance"] for p in peers] == ["host-a"]


def test_discover_peers_skips_non_dict_description(cli, rv):
    rv.register("host-a")
    cli._tunnels["adf-list-payload"] = {
        "tunnelId": "adf-list-payload",
        "labels": [cli._tunnels[_tunnel_id("host-a")]["labels"][0]],
        "description": json.dumps([1, 2, 3]),
    }
    peers = rv.discover_peers()
    assert [p["instance"] for p in peers] == ["host-a"]


def test_discover_peers_skips_missing_instance_field(cli, rv):
    rv.register("host-a")
    cli._tunnels["adf-no-instance"] = {
        "tunnelId": "adf-no-instance",
        "labels": [cli._tunnels[_tunnel_id("host-a")]["labels"][0]],
        "description": json.dumps({"role": "peer"}),
    }
    peers = rv.discover_peers()
    assert [p["instance"] for p in peers] == ["host-a"]


def test_discover_peers_skips_non_string_description_field(cli, rv):
    rv.register("host-a")
    cli._tunnels["adf-dict-description"] = {
        "tunnelId": "adf-dict-description",
        "labels": [cli._tunnels[_tunnel_id("host-a")]["labels"][0]],
        # A malformed/foreign CLI response could hand back a structured
        # description instead of a JSON string; json.loads on a dict/list
        # raises TypeError, not JSONDecodeError -- must not propagate.
        "description": {"instance": "not-a-string-payload"},
    }
    peers = rv.discover_peers()
    assert [p["instance"] for p in peers] == ["host-a"]


# -- transport error handling -----------------------------------------------------


def test_call_raises_devtunnel_error_on_real_failure():
    def failing_runner(args):
        return subprocess.CompletedProcess(["devtunnel"], 1, "", "some other error")

    rv = DevTunnelRendezvous(runner=failing_runner)
    with pytest.raises(DevTunnelError):
        rv.register("host-a")


def test_call_returns_none_on_timeout_marker():
    def timeout_runner(args):
        return None

    rv = DevTunnelRendezvous(runner=timeout_runner)
    with pytest.raises(DevTunnelError):
        rv.register("host-a")


# -- enumeration backoff -----------------------------------------------------


def test_discover_peers_first_failure_raises_and_backs_off():
    clock = FakeClock()
    calls = []

    def flaky_runner(args):
        calls.append(args)
        return subprocess.CompletedProcess(["devtunnel"], 1, "", "not logged in")

    rv = DevTunnelRendezvous(runner=flaky_runner, clock=clock)
    with pytest.raises(DevTunnelError):
        rv.discover_peers()
    assert len(calls) == 1
    # Within the backoff window, a repeated call must NOT re-invoke the CLI.
    assert rv.discover_peers() == []
    assert len(calls) == 1


def test_discover_peers_backoff_doubles_then_recovers_after_elapsing():
    clock = FakeClock()
    calls = []

    def flaky_then_ok_runner(args):
        calls.append(args)
        if len(calls) <= 2:
            return subprocess.CompletedProcess(["devtunnel"], 1, "", "not logged in")
        return subprocess.CompletedProcess(["devtunnel"], 0, json.dumps({"tunnels": []}), "")

    rv = DevTunnelRendezvous(runner=flaky_then_ok_runner, clock=clock)
    with pytest.raises(DevTunnelError):
        rv.discover_peers()
    assert len(calls) == 1
    # First backoff window (5s default) hasn't elapsed -> still cached, no call.
    clock.advance(1)
    rv.discover_peers()
    assert len(calls) == 1
    # Past the first backoff window -> retries, fails again, backoff doubles.
    clock.advance(10)
    with pytest.raises(DevTunnelError):
        rv.discover_peers()
    assert len(calls) == 2
    # Past the doubled window -> retries, succeeds, backoff resets.
    clock.advance(20)
    assert rv.discover_peers() == []
    assert len(calls) == 3
    # Backoff cleared -> the very next call retries immediately (no wait).
    rv.discover_peers()
    assert len(calls) == 4


def test_reap_stale_also_goes_through_backoff():
    clock = FakeClock()
    calls = []

    def failing_runner(args):
        calls.append(args)
        return subprocess.CompletedProcess(["devtunnel"], 1, "", "not logged in")

    rv = DevTunnelRendezvous(runner=failing_runner, clock=clock)
    with pytest.raises(DevTunnelError):
        rv.reap_stale()
    assert len(calls) == 1
    # Still backed off -> reap_stale must not re-invoke list either.
    assert rv.reap_stale() == 0
    assert len(calls) == 1


# -- factory / config wiring -----------------------------------------------------


def test_devtunnel_rendezvous_none_when_backend_not_selected(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_FEDERATION_BACKEND", raising=False)
    assert devtunnel_rendezvous() is None


def test_devtunnel_rendezvous_built_when_selected(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_BACKEND", "devtunnels")
    rv = devtunnel_rendezvous()
    assert isinstance(rv, DevTunnelRendezvous)


def test_rendezvous_from_config_selects_devtunnels_backend(monkeypatch):
    from agent_dispatch.federation_runner import rendezvous_from_config

    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_BACKEND", "devtunnels")
    rv = rendezvous_from_config()
    assert isinstance(rv, DevTunnelRendezvous)


def test_rendezvous_from_config_defaults_to_gateway_backend(monkeypatch):
    from agent_dispatch.federation_runner import rendezvous_from_config

    monkeypatch.delenv("AGENT_DISPATCH_FEDERATION_BACKEND", raising=False)
    monkeypatch.delenv("AGENT_DISPATCH_SHARED_URL", raising=False)
    # No AGENT_DISPATCH_SHARED_URL configured -> the gateway backend is
    # selected but can't be built, confirming the selector did NOT fall
    # through to devtunnels.
    assert rendezvous_from_config() is None


def test_runner_from_config_selects_devtunnels_backend(monkeypatch):
    from agent_dispatch.federation_runner import runner_from_config

    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_ROLE", "peer")
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_INSTANCE", "test-instance")
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_BACKEND", "devtunnels")
    runner = runner_from_config()
    assert runner is not None
    assert isinstance(runner._rv, DevTunnelRendezvous)



def test_federation_backend_defaults_to_gateway(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_FEDERATION_BACKEND", raising=False)
    assert config.federation_backend() == "gateway"


def test_federation_backend_unrecognized_falls_back_to_gateway(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_BACKEND", "bogus")
    assert config.federation_backend() == "gateway"


def test_federation_devtunnel_label_default_and_override(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_FEDERATION_DEVTUNNEL_LABEL", raising=False)
    assert config.federation_devtunnel_label() == "agent-dispatch-federation"
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_DEVTUNNEL_LABEL", "custom-label")
    assert config.federation_devtunnel_label() == "custom-label"
