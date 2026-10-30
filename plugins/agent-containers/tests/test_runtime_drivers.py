"""External runtime driver seam: config, discovery, protocol, routing, launch.

Drives the real ``ExternalDriver`` protocol client against the fake driver
script in ``fake_runtime_driver.py`` (a separate process speaking the JSON
command protocol), so every verb crosses an actual subprocess boundary.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agent_containers import __main__ as cli
from agent_containers import external_driver, fleet, lifecycle, resolver, runtime_drivers
from agent_containers.config import ContainersConfig, FleetConfig, load_config
from agent_containers.external_driver import ExternalDriver, find_driver, scan_drivers
from agent_containers.runtime_drivers import DockerDriver, DriverError

FAKE = Path(__file__).with_name("fake_runtime_driver.py")


@pytest.fixture(autouse=True)
def _isolated_bindings():
    runtime_drivers.clear_bindings()
    yield
    runtime_drivers.clear_bindings()


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """Register the fake driver as ``example-driver`` and return its state."""
    root = tmp_path / "fake-runtime"
    root.mkdir()
    drivers = tmp_path / "drivers.d"
    drivers.mkdir()
    (drivers / "example-driver.json").write_text(json.dumps({
        "schema_version": 1,
        "name": "example-driver",
        "command": [sys.executable, str(FAKE)],
    }))
    monkeypatch.setenv("AGENT_CONTAINERS_DRIVERS_DIR", str(drivers))
    monkeypatch.setenv("FAKE_DRIVER_ROOT", str(root))
    monkeypatch.delenv("FAKE_DRIVER_CAPS", raising=False)
    monkeypatch.delenv("FAKE_DRIVER_FAIL", raising=False)

    def state():
        path = root / "state.json"
        return json.loads(path.read_text()) if path.exists() else {"members": {}, "calls": []}

    return SimpleNamespace(root=root, drivers=drivers, state=state)


@pytest.fixture
def no_docker(monkeypatch):
    """Any Docker CLI use fails, as on a host without Docker."""
    def refuse(args, timeout=30.0):
        raise RuntimeError("docker CLI not found on PATH")

    monkeypatch.setattr(lifecycle, "_docker", refuse)


def driver_fleet(**overrides) -> FleetConfig:
    values = {
        "driver": "example-driver",
        "driver_options": {"profile": "default", "nested": {"cpus": 2}},
        "size": 2,
        "workspace_folder": "/workspaces/example",
        "exec_user": "vscode",
        "relay_enabled": False,
    }
    values.update(overrides)
    return FleetConfig(**values)


def driver_config(**overrides) -> ContainersConfig:
    return ContainersConfig(fleets={"example": driver_fleet(**overrides)})


# -- configuration ---------------------------------------------------------


def test_config_loads_driver_keys(tmp_path, monkeypatch):
    path = tmp_path / "containers.yaml"
    path.write_text(yaml.safe_dump({"fleets": {"example": {
        "driver": "example-driver",
        "driver_options": {"profile": "default"},
        "size": 2,
        "workspace_folder": "/workspaces/example",
    }}}))
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(path))
    selected = load_config(strict=True).fleets["example"]
    assert selected.driver == "example-driver"
    assert selected.driver_options == {"profile": "default"}


def test_docker_default_is_unchanged():
    plain = FleetConfig(image="example/image:1")
    plain.validate_restricted()
    assert plain.driver is None and plain.driver_options == {}
    driver = runtime_drivers.driver_for_fleet(ContainersConfig(), "example", plain)
    assert isinstance(driver, DockerDriver)
    assert driver.capabilities.ssh_transport and not driver.capabilities.owns_relay


@pytest.mark.parametrize("key, value", [
    ("image", "example/image:1"),
    ("devcontainer_path", "/src/example"),
    ("devcontainer_config", ".devcontainer/devcontainer.json"),
    ("repo", "example"),
    ("host_workspace_path", "/srv/example"),
    ("systemd_capable", True),
    ("cpus", 0.0),
])
def test_driver_rejects_docker_provisioning_keys(key, value):
    with pytest.raises(RuntimeError, match=f"driver fleets cannot set {key}"):
        driver_fleet(**{key: value}).validate_restricted()


@pytest.mark.parametrize("overrides, message", [
    ({"security_profile": "restricted"}, "must be trusted"),
    ({"lifecycle_owner": "external"}, "lifecycle_owner"),
    ({"driver": "Bad Name"}, "lowercase driver name"),
    ({"driver": "docker"}, "built-in default"),
    ({"driver_options": ["not", "a", "map"]}, "key/value mapping"),
    ({"driver_options": {"when": object()}}, "JSON-serializable"),
])
def test_driver_validation_errors(overrides, message):
    with pytest.raises(RuntimeError, match=message):
        driver_fleet(**overrides).validate_restricted()


def test_driver_options_require_driver():
    with pytest.raises(RuntimeError, match="requires 'driver'"):
        FleetConfig(image="example/image:1", driver_options={"a": 1}).validate_restricted()


# -- discovery -------------------------------------------------------------


def test_manifest_discovery_and_invalid_entry_finding(fake):
    (fake.drivers / "broken.json").write_text("{not json")
    (fake.drivers / "renamed.json").write_text(json.dumps(
        {"schema_version": 1, "name": "other", "command": [sys.executable]},
    ))
    specs, findings = scan_drivers()
    assert list(specs) == ["example-driver"]
    assert specs["example-driver"].command == (sys.executable, str(FAKE))
    reasons = {Path(f["path"]).name: f["reason"] for f in findings}
    assert "unreadable" in reasons["broken.json"]
    assert "does not match" in reasons["renamed.json"]


def test_path_fallback_and_missing_driver(fake, tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "agent-containers-driver-onpath"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    assert find_driver("onpath").command == (str(exe),)
    with pytest.raises(RuntimeError, match="runtime driver 'absent' is not registered"):
        find_driver("absent")


def test_drivers_cli_lists_manifests(fake, capsys):
    assert cli.main(["drivers", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [d["name"] for d in listed["drivers"]] == ["example-driver"]
    assert listed["findings"] == []


# -- protocol and lifecycle ----------------------------------------------


def test_up_provisions_through_driver_without_docker(fake, no_docker):
    config = driver_config(environment={"EXAMPLE": "1"})
    result = fleet.reconcile_up(config, "example")
    assert result.created == ["example-1", "example-2"]
    assert fleet.reconcile_up(config, "example").created == []

    provision = [c for c in fake.state()["calls"] if c["verb"] == "provision"]
    first = provision[0]["request"]
    assert first["protocolVersion"] == 1
    assert first["fleet"] == "example"
    assert first["driverOptions"] == {"profile": "default", "nested": {"cpus": 2}}
    assert first["member"] == {"name": "example-1", "fleet": "example", "instance": "example-1"}
    assert first["workspaceFolder"] == "/workspaces/example"
    assert first["execUser"] == "vscode"
    assert first["environment"] == {"EXAMPLE": "1"}
    assert {c["protocol_env"] for c in fake.state()["calls"]} == {"1"}


def test_enumerate_inspect_start_stop_remove(fake, no_docker):
    config = driver_config(size=1)
    fleet.reconcile_up(config, "example")

    [member] = lifecycle.list_containers(config)
    assert (member.name, member.fleet, member.driver, member.state) == (
        "example-1", "example", "example-driver", "running",
    )
    assert member.repo == "example-repo"
    assert member.labels["agent-containers.instance"] == "example-1"

    assert fleet.down_fleet(config, "example").stopped == ["example-1"]
    assert lifecycle.inspect_state("example-1") == "exited"
    # Stopped members stay enumerable (warm reuse).
    assert [c.state for c in lifecycle.list_containers(config)] == ["exited"]
    assert lifecycle.list_containers(config, all_containers=False) == []

    assert fleet.start(config, "example") == ["example-1"]
    document = lifecycle.inspect_container("example-1")
    assert document["Id"] == member.container_id
    assert document["State"]["Status"] == "running"
    stop_call = [c for c in fake.state()["calls"] if c["verb"] == "stop"][0]
    assert stop_call["request"]["id"] == member.container_id

    assert fleet.remove_fleet(config, "example", force=True).removed == ["example-1"]
    assert lifecycle.list_containers(config) == []


def test_typed_errors_and_protocol_version(fake, monkeypatch):
    driver = runtime_drivers.driver_for_fleet(driver_config(), "example", driver_fleet())
    monkeypatch.setenv("FAKE_DRIVER_FAIL", "list:runtime_unavailable")
    with pytest.raises(DriverError) as raised:
        driver.list_members()
    assert raised.value.code == "runtime_unavailable"
    assert "example-driver" in str(raised.value)

    monkeypatch.delenv("FAKE_DRIVER_FAIL")
    monkeypatch.setenv("FAKE_DRIVER_PROTOCOL", "2")
    with pytest.raises(DriverError) as raised:
        driver.list_members()
    assert raised.value.code == "unsupported_protocol"


def test_unavailable_driver_does_not_hide_docker_fleets(fake, monkeypatch):
    config = ContainersConfig(fleets={
        "example": driver_fleet(),
        "plain": FleetConfig(image="example/image:1"),
    })
    monkeypatch.setenv("FAKE_DRIVER_FAIL", "list:runtime_unavailable")
    row = "\t".join([
        "plain-1", "b" * 64, "example/image:1", "running", "Up",
        "agent-containers.fleet=plain", "trusted", "", "",
    ])
    monkeypatch.setattr(lifecycle, "_docker", lambda args, timeout=30.0:
                        subprocess.CompletedProcess(args, 0, row + "\n", ""))
    assert [c.name for c in lifecycle.list_containers(config)] == ["plain-1"]
    # A fleet operation on the driver fleet itself fails loudly.
    with pytest.raises(DriverError):
        fleet.down_fleet(config, "example")


def test_docker_failure_still_raises_with_docker_fleets(fake, no_docker):
    config = ContainersConfig(fleets={
        "example": driver_fleet(), "plain": FleetConfig(image="example/image:1"),
    })
    with pytest.raises(RuntimeError, match="docker CLI not found"):
        lifecycle.list_containers(config)


# -- exec, streaming exec and copy ---------------------------------------


def _provisioned(config):
    fleet.reconcile_up(config, "example")
    return {c.name: c for c in lifecycle.list_containers(config)}["example-1"]


def test_captured_exec_and_copy_route_through_driver(fake, no_docker, tmp_path, monkeypatch):
    member = _provisioned(driver_config(size=1))
    monkeypatch.setenv("EXAMPLE_SECRET", "from-host")
    done = runtime_drivers.run_routed([
        "docker", "exec", "-i", "-u", "vscode", "-e", "GREETING=hello",
        "-e", "EXAMPLE_SECRET", "-w", "/work", member.container_id,
        "sh", "-c", 'printf "%s %s %s %s " "$GREETING" "$EXAMPLE_SECRET" '
        '"$FAKE_MEMBER_USER" "$(basename "$PWD")"; cat',
    ], input_text="from-stdin")
    assert done.returncode == 0
    assert done.stdout == "hello from-host vscode work from-stdin"
    exec_call = [c for c in fake.state()["calls"] if c["verb"] == "exec"][-1]
    assert exec_call["request"]["name"] == "example-1"

    source = tmp_path / "payload.txt"
    source.write_text("payload")
    copied = runtime_drivers.run_routed(
        ["docker", "cp", str(source), "example-1:/opt/payload.txt"],
    )
    assert copied.returncode == 0
    assert (fake.root / "members/example-1/opt/payload.txt").read_text() == "payload"

    # Unowned targets keep the Docker path untouched.
    assert runtime_drivers.run_routed(["docker", "exec", "plain-1", "true"]) is None
    argv = ["docker", "exec", "-i", "-u", "root", "plain-1", "sshd"]
    assert runtime_drivers.stream_routed(argv) is argv


def test_streaming_exec_passes_stdio_through(fake, no_docker, monkeypatch):
    _provisioned(driver_config(size=1))
    monkeypatch.setenv("EXAMPLE_SECRET", "s3cret")
    argv = runtime_drivers.stream_routed([
        "docker", "exec", "-i", "-e", "EXAMPLE_SECRET", "-u", "vscode", "example-1",
        "sh", "-c", 'printf "%s:" "$EXAMPLE_SECRET"; cat',
    ])
    assert argv[:3] == [sys.executable, str(FAKE), "exec-stream"]
    assert "s3cret" not in " ".join(argv)
    request = json.loads(argv[argv.index("--request") + 1])
    assert request["inheritEnv"] == ["EXAMPLE_SECRET"]
    assert request["driverOptions"]["profile"] == "default"
    done = subprocess.run(argv, input=b"streamed-bytes", capture_output=True, timeout=60)
    assert done.returncode == 0
    assert done.stdout == b"s3cret:streamed-bytes"


# -- capabilities and launch -----------------------------------------------


@pytest.mark.parametrize("caps, relay, expected", [
    ({}, False, (False, False)),
    ({"owns_relay": True}, True, (False, False)),
    ({"ssh_transport": True}, True, (True, True)),
    ({"owns_relay": True, "ssh_transport": True}, True, (True, False)),
])
def test_launch_plan_capabilities(fake, no_docker, monkeypatch, caps, relay, expected):
    monkeypatch.setenv("FAKE_DRIVER_CAPS", json.dumps(caps))
    config = driver_config(size=1, relay_enabled=relay)
    _provisioned(config)
    plan = runtime_drivers.launch_plan(config.fleets["example"], "example-1", relay)
    assert (plan.ssh, plan.agent_relay) == expected


def test_relay_without_capability_fails_closed(fake, no_docker):
    config = driver_config(size=1, relay_enabled=True)
    _provisioned(config)
    with pytest.raises(RuntimeError, match="neither owns the relay"):
        runtime_drivers.launch_plan(config.fleets["example"], "example-1", True)


def _write_config(tmp_path, monkeypatch, **overrides):
    raw = {"driver": "example-driver", "size": 1,
           "workspace_folder": "/workspaces/example", "exec_user": "vscode",
           "copilot_auth_mode": "legacy"}
    raw.update(overrides)
    path = tmp_path / "containers.yaml"
    path.write_text(yaml.safe_dump({"fleets": {"example": raw}}))
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(path))
    return load_config(strict=True)


def test_resolver_routes_driver_member_through_exec_wrapper(fake, no_docker, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DRIVER_CAPS", json.dumps({"owns_relay": True}))
    config = _write_config(tmp_path, monkeypatch, relay_enabled=True)
    fleet.reconcile_up(config, "example")

    spec = asyncio.run(resolver.ContainerResolver().resolve_spec("example-1"))
    assert spec["spawn_command"][-3:] == ["exec", "--stdio", "example-1"]
    assert "container" not in spec  # no Session Host / SSH projection
    venue = spec["venue"]
    assert venue["transport"] == "driver-exec"
    assert venue["driver"] == "example-driver"
    assert venue["capabilities"]["credential_relay"] is True
    assert venue["capabilities"]["session_host"] is False
    assert venue["ready"] is True
    assert asyncio.run(resolver.ContainerResolver().target_repo("example-1")) == "example-repo"
    asyncio.run(resolver.ContainerResolver().ensure_ready("example-1"))


def test_resolver_fails_closed_on_missing_relay_capability(fake, no_docker, tmp_path, monkeypatch):
    config = _write_config(tmp_path, monkeypatch, relay_enabled=True)
    fleet.reconcile_up(config, "example")
    with pytest.raises(RuntimeError, match="neither owns the relay"):
        asyncio.run(resolver.ContainerResolver().resolve_spec("example-1"))


def test_exec_launches_acp_through_driver_stream(fake, no_docker, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DRIVER_CAPS", json.dumps({"owns_relay": True}))
    config = _write_config(tmp_path, monkeypatch, relay_enabled=True)
    fleet.reconcile_up(config, "example")
    seen = {}
    monkeypatch.setattr(cli.hosting_policy, "require_container_hosting", lambda *a, **k: None)
    monkeypatch.setattr(cli, "host_gh_token", lambda: "gh-token")
    monkeypatch.setattr(cli, "prepare_ssh_config", lambda *a: pytest.fail("no SSH"))
    monkeypatch.setattr(cli, "_require_live_relay_port", lambda: pytest.fail("no relay"))
    monkeypatch.setattr(cli, "write_remote_env", lambda name, user, values:
                        seen.setdefault("staged", dict(values)) and "/launch/1.env")
    monkeypatch.setattr(cli, "cleanup_remote_env", lambda *a: None)
    monkeypatch.setattr(cli, "container_environment", lambda *a: {"PATH": "/usr/bin"})
    monkeypatch.setattr(cli, "_exec_stdio", lambda argv, env:
                        seen.update(argv=argv, env=env) or 0)

    args = SimpleNamespace(name="example-1", stdio=True, force=False, copilot_args=None)
    assert cli._cmd_exec(args) == 0

    argv = seen["argv"]
    assert argv[:3] == [sys.executable, str(FAKE), "exec-stream"]
    request = json.loads(argv[argv.index("--request") + 1])
    assert request["user"] == "vscode"
    assert request["argv"][:2] == ["sh", "-c"]
    assert "copilot --acp" in request["argv"][2]
    assert "gh-token" not in " ".join(argv)
    assert seen["staged"]["GH_TOKEN"] == "gh-token"
    # The driver owns the relay: agent-containers injects none of its wiring.
    assert not any(name.startswith("LC_GIT_CREDENTIAL_RELAY") for name in seen["staged"])
    assert not any(name.startswith("LC_GIT_CREDENTIAL_RELAY") for name in seen["env"])


def test_copilot_verb_requires_ssh_capability(fake, no_docker, tmp_path, monkeypatch):
    config = _write_config(tmp_path, monkeypatch, relay_enabled=False)
    fleet.reconcile_up(config, "example")
    with pytest.raises(RuntimeError, match="needs the trusted SSH transport"):
        runtime_drivers.require_ssh_transport(config, "example-1", "The `copilot` verb")


def test_lease_borrow_and_fleet_listing_cover_driver_members(fake, no_docker, tmp_path, monkeypatch, capsys):
    config = _write_config(tmp_path, monkeypatch, size=2)
    fleet.reconcile_up(config, "example")
    from agent_containers.lease import borrow, release

    lease = borrow(config, "example-effort", fleet="example")
    assert lease.container in {"example-1", "example-2"}
    assert release(lease.container)

    assert cli.main(["fleet", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert {(c["name"], c["driver"]) for c in listed} == {
        ("example-1", "example-driver"), ("example-2", "example-driver"),
    }


def test_external_driver_rejects_foreign_member_reports(fake):
    driver = ExternalDriver(find_driver("example-driver"), "example", driver_fleet())
    with pytest.raises(DriverError) as raised:
        driver._member_or_error("list", {"name": "x-1", "id": "x1", "fleet": "other",
                                          "state": "running"})
    assert raised.value.code == "protocol_error"
    assert external_driver.PROTOCOL_VERSION == 1
