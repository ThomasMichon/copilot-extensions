"""Offline-first controls; no Docker, host kernel inventory or real sockets."""

from __future__ import annotations

import copy
import errno
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from test_reproducer_lab_boundary import declaration, inspection
from test_reproducer_lab_monitoring import LAB, FakeDocker, experiment, resources

import boundary
import network
import offline
import reader

ZERO, LOOP = "0" * 32, "0" * 31 + "1"
ROUTE = f"{ZERO} 00 {ZERO} 00 {ZERO} ffffffff 00000001 00000000 00200200 lo\n"
ADDRESS = f"{LOOP} 01 80 10 80 lo\n"


def snapshot() -> dict[str, Any]:
    return {
        "namespace_inode": 42, "interfaces": [[1, "lo"]],
        "loopback_flags": "0x9\n",
        "ipv4_routes": "\t".join(offline._HEADER) + "\n",
        "ipv6_routes": ROUTE, "ipv6_addresses": ADDRESS,
    }


def observation() -> dict[str, Any]:
    return {
        "before": snapshot(), "after": snapshot(),
        "ipv4_error": "ENETUNREACH", "ipv6_error": "ENETUNREACH",
    }


def selected() -> boundary.Experiment:
    return replace(experiment(), network_mode="none")


def test_existing_v1_defaults_remain_internal_and_none_is_explicit() -> None:
    raw = declaration()
    original = boundary.parse_declaration(raw)
    assert original.network_mode == "internal"
    assert original.network_target == original.resource_prefix + "-internal"
    raw["network_mode"] = "internal"
    assert boundary.parse_declaration(raw) == original
    raw["network_mode"] = "none"
    assert boundary.parse_declaration(raw).network_target == "none"
    raw["unknown"] = True
    with pytest.raises(ValueError):
        boundary.parse_declaration(raw)


@pytest.mark.parametrize("mode", ["host", "bridge", "container:other", "", None, True, 1, {}, []])
def test_unknown_network_modes_never_default_to_a_supported_mode(mode: object) -> None:
    raw = declaration()
    raw["network_mode"] = mode
    with pytest.raises(ValueError):
        boundary.parse_declaration(raw)
    with pytest.raises(ValueError):
        replace(experiment(), network_mode="unsupported").network_target


def test_static_none_selection_preserves_all_unresolved_gates() -> None:
    value = inspection()
    value["HostConfig"]["NetworkMode"] = "none"
    observed = boundary.inspect_static_boundary(selected(), value)
    assert observed.violations == () and len(observed.unresolved) == 5
    assert any("egress" in item for item in observed.unresolved)
    assert boundary.inspect_static_boundary(experiment(), value).violations
    assert boundary.inspect_static_boundary(selected(), inspection()).violations


def test_modes_cannot_mix_internal_peer_inventory_or_offline_probe() -> None:
    fake = FakeDocker()
    host = reader.DockerReader(Path("/selected/docker"), run=fake)
    with pytest.raises(ValueError, match="internal mode"):
        host.network_inventory(selected(), "c" * 64)
    with pytest.raises(ValueError, match="internal mode"):
        network.inspect_inventory(selected(), ("c" * 64,), [], [])
    with pytest.raises(ValueError, match="explicit none"):
        host.offline_network(experiment(), resources())
    assert fake.calls == []


def test_loopback_observation_is_evidence_not_custody_or_admission() -> None:
    value = observation()
    before = copy.deepcopy(value)
    result = offline.inspect_observation(value)
    assert result == offline.OfflineNetwork(42, 1, 9, 1, 1)
    assert not hasattr(result, "ready")
    assert value == before


@pytest.mark.parametrize("field,value", [
    ("namespace_inode", 0), ("namespace_inode", True), ("namespace_inode", 2**64),
    ("interfaces", []), ("interfaces", [[1, "lo"], [2, "eth0"]]),
    ("interfaces", [[1, "eth0"]]), ("interfaces", [[True, "lo"]]),
    ("interfaces", [[0, "lo"]]), ("interfaces", [[2**31, "lo"]]),
    ("loopback_flags", "0x8\n"), ("loopback_flags", "0x1\n"),
    ("loopback_flags", "not-hex"), ("loopback_flags", None),
    ("ipv4_routes", ""), ("ipv4_routes", "wrong header\n"),
    ("ipv4_routes", "\t".join(offline._HEADER) + "\nlo 0 0 0 0 0 0 0 0 0 0\n"),
    ("ipv6_routes", ROUTE.replace("lo", "eth0")),
    ("ipv6_routes", ROUTE.replace(ZERO + " 00", LOOP + " 00", 1)),
    ("ipv6_routes", ROUTE.replace("ffffffff", "unknown")),
    ("ipv6_addresses", ADDRESS.replace("lo", "eth0")),
    ("ipv6_addresses", ADDRESS.replace(LOOP, "1" * 32)),
    ("ipv6_addresses", ADDRESS.replace(" 01 ", " 02 ")),
    ("ipv6_addresses", ADDRESS.replace(" 80 10 ", " 40 00 ")),
    ("ipv6_routes", "\u00e9"), ("ipv6_routes", "x" * (8192 + 1)),
    ("ipv6_routes", ROUTE * (offline.MAX_RECORDS + 1)),
])
def test_foreign_malformed_or_unbounded_kernel_observations_refuse(field: str, value: object) -> None:
    observed = observation()
    observed["before"][field] = value
    with pytest.raises(ValueError):
        offline.inspect_observation(observed)


def test_exact_kernel_record_ceiling_and_unconfigured_ipv6_are_supported() -> None:
    value = observation()
    for side in ("before", "after"):
        value[side]["ipv6_routes"] = ROUTE * offline.MAX_RECORDS
    assert len(value["before"]["ipv6_routes"]) < offline.MAX_FILE_BYTES
    assert offline.inspect_observation(value).ipv6_route_count == offline.MAX_RECORDS
    for side in ("before", "after"):
        value[side]["ipv6_routes"] += ROUTE
    assert len(value["before"]["ipv6_routes"]) < offline.MAX_FILE_BYTES
    with pytest.raises(ValueError, match="record ceiling"):
        offline.inspect_observation(value)
    for side in ("before", "after"):
        value[side]["ipv6_routes"] = value[side]["ipv6_addresses"] = ""
    assert offline.inspect_observation(value).ipv6_route_count == 0


def test_loopback_host_route_is_supported_but_no_external_route_is_added() -> None:
    value = observation()
    host = f"{LOOP} 80 {ZERO} 00 {ZERO} 00000000 00000001 00000000 80200001 lo\n"
    for side in ("before", "after"):
        value[side]["ipv6_routes"] += host
    assert offline.inspect_observation(value).ipv6_route_count == 2


def test_volatile_route_counters_do_not_fake_a_configuration_change() -> None:
    value = observation()
    value["after"]["ipv6_routes"] = ROUTE.replace("00000001 00000000", "00000002 00000001")
    assert offline.inspect_observation(value).namespace_inode == 42
    value["after"]["ipv6_routes"] = ROUTE.replace("ffffffff", "fffffffe")
    with pytest.raises(ValueError, match="changed during"):
        offline.inspect_observation(value)


@pytest.mark.parametrize("field,value", [
    ("ipv4_error", "ECONNREFUSED"), ("ipv6_error", "EAFNOSUPPORT"),
    ("ipv4_error", "timeout"), ("ipv6_error", None),
])
def test_only_actual_no_route_results_for_both_families_are_accepted(field: str, value: object) -> None:
    observed = observation()
    observed[field] = value
    with pytest.raises(ValueError, match="no-route"):
        offline.inspect_observation(observed)


def test_missing_fields_changed_namespace_and_extra_claims_refuse() -> None:
    value = observation()
    value["after"]["namespace_inode"] = 43
    with pytest.raises(ValueError, match="changed during"):
        offline.inspect_observation(value)
    value = observation()
    value["ready"] = True
    with pytest.raises(ValueError):
        offline.inspect_observation(value)
    value = observation()
    del value["before"]["interfaces"]
    with pytest.raises(ValueError):
        offline.inspect_observation(value)


def test_guest_checks_loopback_before_any_socket_attempt_and_bounds_elapsed_time(monkeypatch: Any) -> None:
    calls: list[tuple[int, tuple]] = []

    def no_route(family: int, address: tuple) -> str:
        calls.append((family, address))
        return "ENETUNREACH"

    monkeypatch.setattr(offline, "_snapshot", snapshot)
    monkeypatch.setattr(offline, "_no_route", no_route)
    assert offline.observe()["ipv4_error"] == "ENETUNREACH"
    assert [address for _family, address in calls] == [
        ("192.0.2.1", 9), ("2001:db8::1", 9, 0, 0),
    ]
    calls.clear()
    foreign = snapshot()
    foreign["interfaces"].append([2, "eth0"])
    monkeypatch.setattr(offline, "_snapshot", lambda: foreign)
    with pytest.raises(ValueError, match="loopback"):
        offline.observe()
    assert calls == []
    monkeypatch.setattr(offline, "_snapshot", snapshot)
    clock = iter([0, 5])
    monkeypatch.setattr(offline, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    with pytest.raises(TimeoutError):
        offline.observe()
    with pytest.raises(TimeoutError):
        offline._expired(0, None)


@pytest.mark.parametrize("outcome", [errno.ENETUNREACH, errno.ECONNREFUSED, errno.EPERM, "timeout", None])
def test_no_route_socket_is_finite_closed_and_never_converts_other_outcomes(
    monkeypatch: Any, outcome: int | str | None,
) -> None:
    class Channel:
        closed = False

        def __enter__(self) -> Channel:
            return self

        def __exit__(self, *_error: object) -> None:
            self.closed = True

        def settimeout(self, seconds: float) -> None:
            assert seconds == 1

        def connect(self, address: tuple) -> None:
            assert address == ("192.0.2.1", 9)
            if outcome == "timeout":
                raise TimeoutError("synthetic-timeout")
            if isinstance(outcome, int):
                raise OSError(outcome, "synthetic-result")

    channel = Channel()
    monkeypatch.setattr(offline, "socket", SimpleNamespace(socket=lambda *_args: channel, SOCK_STREAM=1))
    if outcome == errno.ENETUNREACH:
        assert offline._no_route(2, ("192.0.2.1", 9)) == "ENETUNREACH"
    else:
        with pytest.raises(ValueError):
            offline._no_route(2, ("192.0.2.1", 9))
    assert channel.closed


def test_kernel_file_reader_stops_at_its_byte_ceiling(tmp_path: Path, monkeypatch: Any) -> None:
    path = tmp_path / "kernel"
    path.write_bytes(b"x" * offline.MAX_FILE_BYTES)
    monkeypatch.setattr(offline, "open", lambda *_args: path.open("rb"), raising=False)
    assert len(offline._read("synthetic")) == offline.MAX_FILE_BYTES
    path.write_bytes(b"x" * (offline.MAX_FILE_BYTES + 1))
    with pytest.raises(ValueError, match="ceiling"):
        offline._read("synthetic")


def test_snapshot_reads_only_the_fixed_namespace_and_kernel_paths(monkeypatch: Any) -> None:
    value = snapshot()
    files = {
        "/sys/class/net/lo/flags": value["loopback_flags"],
        "/proc/net/route": value["ipv4_routes"],
        "/proc/net/ipv6_route": value["ipv6_routes"],
        "/proc/net/if_inet6": value["ipv6_addresses"],
    }
    requested: list[str] = []

    def read(path: str) -> str:
        requested.append(path)
        return files[path]

    def stat(path: str) -> SimpleNamespace:
        assert path == "/proc/self/ns/net"
        return SimpleNamespace(st_ino=42)

    monkeypatch.setattr(offline, "_read", read)
    monkeypatch.setattr(offline, "os", SimpleNamespace(stat=stat))
    monkeypatch.setattr(offline, "socket", SimpleNamespace(if_nameindex=lambda: [(1, "lo")]))
    assert offline._snapshot() == value
    assert set(requested) == set(files)


def test_unavailable_socket_family_is_not_reported_as_observed_refusal(monkeypatch: Any) -> None:
    def unavailable(*_args: object) -> object:
        raise OSError(errno.EAFNOSUPPORT, "synthetic-unavailable")

    monkeypatch.setattr(offline, "socket", SimpleNamespace(socket=unavailable, SOCK_STREAM=1))
    with pytest.raises(OSError):
        offline._no_route(2, ("192.0.2.1", 9))


class OfflineDocker(FakeDocker):
    def __init__(self) -> None:
        super().__init__()
        self.container["HostConfig"]["NetworkMode"] = "none"
        self.value = observation()

    def __call__(self, argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        if argv[1] != "exec":
            return super().__call__(argv, timeout=timeout)
        self.calls.append((argv, timeout))
        assert argv[1:8] == ["exec", "--user", "1000:1000", "c" * 64, "/usr/bin/python3", "-I", "-c"]
        assert argv[8] == (LAB / "offline.py").read_text()
        return subprocess.CompletedProcess(argv, 0, json.dumps(self.value), "")


def test_finite_offline_reader_pins_resources_without_inspecting_a_bridge() -> None:
    fake = OfflineDocker()
    result = reader.DockerReader(Path("/selected/docker"), run=fake).offline_network(selected(), resources())
    assert result.namespace_inode == 42
    assert [argv[1] for argv, _timeout in fake.calls] == ["container", "volume", "exec", "container", "volume"]
    assert all(0 < timeout <= 10 for _argv, timeout in fake.calls)


@pytest.mark.parametrize("failure_at", range(1, 6))
def test_failed_offline_command_never_retries_or_returns_an_observation(failure_at: int) -> None:
    fake = OfflineDocker()

    def run(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        if len(fake.calls) + 1 == failure_at:
            fake.calls.append((argv, timeout))
            raise subprocess.TimeoutExpired(argv, timeout)
        return fake(argv, timeout=timeout)

    with pytest.raises(reader.ReadError):
        reader.DockerReader(Path("/selected/docker"), run=run).offline_network(selected(), resources())
    assert len(fake.calls) == failure_at


def test_offline_reader_refuses_final_expiry_replacement_and_malformed_probe() -> None:
    fake = OfflineDocker()
    now = 0.0

    def run(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        nonlocal now
        result = fake(argv, timeout=timeout)
        if len(fake.calls) == 5:
            now = 30
        return result

    with pytest.raises(reader.ReadError, match="deadline"):
        reader.DockerReader(Path("/selected/docker"), run=run, clock=lambda: now).offline_network(
            selected(), resources(),
        )
    fake = OfflineDocker()
    fake.value["before"]["interfaces"] = []
    with pytest.raises(reader.ReadError, match="loopback"):
        reader.DockerReader(Path("/selected/docker"), run=fake).offline_network(selected(), resources())
    fake = OfflineDocker()

    def replace_volume(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        result = fake(argv, timeout=timeout)
        if len(fake.calls) == 3:
            fake.volumes[0]["CreatedAt"] = "replaced"
        return result

    with pytest.raises(reader.ReadError, match="changed during"):
        reader.DockerReader(Path("/selected/docker"), run=replace_volume).offline_network(selected(), resources())
