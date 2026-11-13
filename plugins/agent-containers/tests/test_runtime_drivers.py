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
_REAL_DOCKER = lifecycle._docker
# Cases that run POSIX shell commands inside the fake "member", rely on
# shebang/ENOEXEC semantics, or on exec-stream's in-place exec.
posix_only = pytest.mark.skipif(os.name == "nt", reason="needs POSIX shell/exec semantics")


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


@posix_only
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


def test_podman_bundled_fallback_preserves_explicit_drivers(fake, monkeypatch):
    monkeypatch.setattr(external_driver.shutil, "which", lambda _: None)
    spec = find_driver("podman")
    assert spec.command == (sys.executable, "-I", "-B", "-X", "utf8", "-m",
                            "agent_containers.podman_driver")
    assert spec.source == "bundled"
    monkeypatch.setattr(external_driver.shutil, "which", lambda _: "/tools/custom-podman")
    assert find_driver("podman").source == "PATH"
    (fake.drivers / "podman.json").write_text(json.dumps({
        "schema_version": 1, "name": "podman", "command": [sys.executable, "custom.py"],
    }), encoding="utf-8")
    assert find_driver("podman").command == (sys.executable, "custom.py")


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.parametrize("shadow_location", ("checkout", "pythonpath"))
def test_bundled_podman_imports_installed_driver_not_checkout(
    fake, tmp_path, monkeypatch, shadow_location,
):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shadow = checkout if shadow_location == "checkout" else tmp_path / "injected"
    package = shadow / "agent_containers"
    package.mkdir(parents=True)
    marker = tmp_path / "checkout-imported"
    (package / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\nraise SystemExit(0)\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("PYTHONPATH", str(shadow))
    monkeypatch.setattr(external_driver.shutil, "which", lambda _: None)
    driver = ExternalDriver(
        find_driver("podman"), "example",
        FleetConfig(driver="podman", driver_options={"image": "example"}),
    )
    original = driver._request
    driver._request = lambda payload: {**original(payload), "protocolVersion": 2}
    with pytest.raises(DriverError) as raised:
        driver.call("capabilities")
    assert raised.value.code == "unsupported_protocol"
    assert not marker.exists()


@pytest.mark.parametrize("component", ("engine", "driver"))
def test_readiness_podman_lookup_excludes_checkout(fake, tmp_path, monkeypatch, component):
    from agent_containers.podman_driver import PodmanDriver

    tools = tmp_path / "host-tools"
    tools.mkdir()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    name = "podman" if component == "engine" else "agent-containers-driver-podman"
    filename = name + (".exe" if os.name == "nt" else "")
    approved = tools / filename
    for candidate in (approved, checkout / filename):
        candidate.write_bytes(b"fixture executable")
        candidate.chmod(0o755)
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("PATH", "." + os.pathsep + str(tools))
    monkeypatch.setenv("NoDefaultCurrentDirectoryInExePath", "1")
    command = (
        PodmanDriver({
            "protocolVersion": 1, "driver": "podman", "fleet": "example",
            "driverOptions": {"image": "example"},
        }).command if component == "engine" else find_driver("podman").command
    )
    assert Path(command[0]) == approved


@pytest.mark.parametrize("filename", ["podman.json", "PODMAN.json", "podman.JSON", "PODMAN.JSON"])
def test_invalid_explicit_podman_does_not_fall_back(fake, monkeypatch, filename):
    (fake.drivers / filename).write_text("{broken")
    monkeypatch.setattr(external_driver.shutil, "which", lambda _: "/tools/custom-podman")
    with pytest.raises(RuntimeError, match="unreadable manifest"):
        find_driver("podman")


def test_non_podman_suffix_preserves_platform_glob_behavior(fake):
    original = fake.drivers / "example-driver.json"
    original.rename(fake.drivers / "example-driver.JSON")
    specs, findings = scan_drivers()
    assert ("example-driver" in specs) == (os.name == "nt")
    assert findings == []


def test_podman_directory_scan_failure_refuses_fallback(fake, monkeypatch):
    original_iterdir = Path.iterdir

    def unreadable(path):
        if path == fake.drivers:
            raise PermissionError("fixture directory unavailable")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", unreadable)
    lookups = []
    monkeypatch.setattr(external_driver.shutil, "which", lambda name: (
        lookups.append(name) or "/tools/custom-podman"
    ))
    with pytest.raises(RuntimeError, match="cannot enumerate"):
        find_driver("podman")
    assert lookups == []


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
    # Addressed by name: the current incarnation, like `docker stop <name>`.
    stop_call = [c for c in fake.state()["calls"] if c["verb"] == "stop"][0]
    assert stop_call["request"]["name"] == "example-1"
    assert "id" not in stop_call["request"]

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


@posix_only
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


@posix_only
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
    for bad in (
        {"name": "x-1", "id": "0123456789ab", "fleet": "other", "state": "running"},
        {"name": "example-9", "id": "not-hex-id", "fleet": "example", "state": "running"},
    ):
        with pytest.raises(DriverError) as raised:
            driver._member_or_error("list", bad)
        assert raised.value.code == "protocol_error"
    assert external_driver.PROTOCOL_VERSION == 1


# -- incarnation pinning and routing handles ---------------------------------


def test_stale_incarnation_is_refused_after_recreation(fake, no_docker):
    config = driver_config(size=1)
    old = _provisioned(config)
    lifecycle.remove_container("example-1", force=True)
    fleet.reconcile_up(config, "example")
    new = {c.name: c for c in lifecycle.list_containers(config)}["example-1"]
    assert new.container_id != old.container_id

    with pytest.raises(DriverError) as raised:
        lifecycle.remove_container(old.container_id, force=True)
    assert raised.value.code == "conflict"
    with pytest.raises(DriverError):
        runtime_drivers.run_routed(["docker", "exec", old.container_id, "true"])
    with pytest.raises(lifecycle.ContainerNotFoundError):
        lifecycle.inspect_container(old.container_id)
    stale_stream = runtime_drivers.stream_routed(
        ["docker", "exec", "-i", old.container_id, "true"],
    )
    assert json.loads(stale_stream[stale_stream.index("--request") + 1])["id"] == old.container_id
    assert subprocess.run(stale_stream, capture_output=True, timeout=60).returncode == 125
    # The replacement is untouched and still addressable by name and new id.
    assert lifecycle.inspect_state("example-1") == "running"
    assert lifecycle.inspect_container(new.container_id)["Id"] == new.container_id


def test_handles_reported_by_two_runtimes_are_refused(fake, no_docker):
    config = ContainersConfig(fleets={
        "example": driver_fleet(size=1),
        "other": driver_fleet(size=1, name_prefix="other"),
    })
    fleet.reconcile_up(config, "example")
    fleet.reconcile_up(config, "other")
    members = {c.name: c for c in lifecycle.list_containers(config)}
    assert set(members) == {"example-1", "other-1"}
    clash = lifecycle.DockerContainerInfo(
        name="other-1", container_id=members["example-1"].container_id,
        image="", state="running", status="", fleet="other",
    )
    other_driver = runtime_drivers.owner_of("other-1").driver
    runtime_drivers.bind_member(other_driver, clash)
    with pytest.raises(RuntimeError, match="more than one runtime driver"):
        lifecycle.remove_container(members["example-1"].container_id, force=True)
    assert lifecycle.inspect_state("example-1") == "running"


@posix_only
def test_shared_docker_executor_routes_driver_members(fake, tmp_path, monkeypatch):
    empty = tmp_path / "no-docker-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))  # no Docker CLI on this host
    member = _provisioned(driver_config(size=1))
    done = lifecycle._docker(
        ["exec", "-u", "vscode", member.container_id, "/bin/sh", "-c", "echo routed"],
    )
    assert (done.returncode, done.stdout) == (0, "routed\n")


def test_standalone_session_host_state_discovers_driver_members(
    fake, no_docker, tmp_path, monkeypatch, capsys,
):
    config = _write_config(tmp_path, monkeypatch)
    fleet.reconcile_up(config, "example")
    member_id = {c.name: c for c in lifecycle.list_containers(config)}["example-1"].container_id
    runtime = runtime_drivers.runtime_identity("example-1")  # in prepare's state_command
    runtime_drivers.clear_bindings()  # a fresh CLI process
    assert cli.main(["session-host-state", "example-1", "--expected-instance", member_id,
                     "--expected-runtime", runtime]) == 0
    state = json.loads(capsys.readouterr().out)
    assert (state["state"], state["container_id"]) == ("running", member_id)


def test_ssh_only_launches_require_ssh_capability(fake, no_docker, monkeypatch):
    config = driver_config(size=1, relay_enabled=True)
    monkeypatch.setenv("FAKE_DRIVER_CAPS", json.dumps({"owns_relay": True}))
    _provisioned(config)
    with pytest.raises(RuntimeError, match="needs the trusted SSH transport"):
        runtime_drivers.launch_plan(config.fleets["example"], "example-1", True, require_ssh=True)


def _docker_rows(monkeypatch, *rows):
    text = "".join("\t".join(row) + "\n" for row in rows)
    monkeypatch.setattr(lifecycle, "_docker", lambda args, timeout=30.0:
                        subprocess.CompletedProcess(args, 0, text, ""))


def _row(name, container_id, fleet_label):
    return [name, container_id, "example/image:1", "running", "Up",
            f"agent-containers.fleet={fleet_label}", "trusted", "", ""]


def test_docker_rows_claiming_a_driver_fleet_are_never_managed_around_it(fake, monkeypatch):
    from agent_containers.lifecycle_ownership import DRIVER_REASON, lifecycle_blocker

    config = ContainersConfig(fleets={
        "example": driver_fleet(), "plain": FleetConfig(image="example/image:1"),
    })
    monkeypatch.setenv("FAKE_DRIVER_FAIL", "list:runtime_unavailable")
    _docker_rows(monkeypatch, _row("example-7", "c" * 64, "example"), _row("plain-1", "b" * 64, "plain"))
    assert [c.name for c in lifecycle.list_containers(config)] == ["plain-1"]
    labels = {"agent-containers.fleet": "example"}
    assert lifecycle_blocker(config, "example-7", labels) == DRIVER_REASON
    assert lifecycle_blocker(config, "example-8", {}) == DRIVER_REASON
    assert lifecycle_blocker(config, "plain-1", {"agent-containers.fleet": "plain"}) is None


def test_docker_and_driver_handle_collisions(fake, monkeypatch):
    config = ContainersConfig(fleets={
        "example": driver_fleet(size=2), "plain": FleetConfig(image="example/image:1"),
    })
    _docker_rows(monkeypatch)
    fleet.reconcile_up(config, "example")
    members = {c.name: c for c in lifecycle.list_containers(config)}
    same = members["example-1"]
    # The same container seen through the Docker CLI is shown once ...
    _docker_rows(monkeypatch, _row("example-1", same.container_id, "plain"),
                 _row("example-2", "d" * 64, "plain"))
    listed = lifecycle.list_containers(config)
    assert sorted((c.name, c.driver or "docker") for c in listed) == [
        ("example-1", "example-driver"),
        ("example-2", "docker"), ("example-2", "example-driver"),
    ]
    # ... while a distinct Docker container sharing a name is never routed.
    with pytest.raises(RuntimeError, match="more than one runtime driver"):
        lifecycle.stop_container("example-2")
    assert lifecycle.inspect_state("example-1") == "running"


@posix_only
def test_unlaunchable_driver_is_a_typed_error_and_skipped(fake, no_docker):
    script = fake.root / "not-a-program"
    script.write_text("this is not a program\n")
    script.chmod(0o755)
    (fake.drivers / "broken-exec.json").write_text(json.dumps(
        {"schema_version": 1, "name": "broken-exec", "command": [str(script)]},
    ))
    config = ContainersConfig(fleets={
        "example": driver_fleet(size=1),
        "broken": driver_fleet(driver="broken-exec"),
    })
    fleet.reconcile_up(config, "example")
    with pytest.raises(DriverError) as raised:
        runtime_drivers.driver_for_fleet(config, "broken", config.fleets["broken"]).list_members()
    assert raised.value.code == "runtime_unavailable"
    assert [c.name for c in lifecycle.list_containers(config)] == ["example-1"]


@pytest.mark.parametrize("caps, failing", [({"ssh_transport": True}, True), ({}, False)])
def test_readiness_requires_ssh_only_for_ssh_capable_drivers(fake, monkeypatch, caps, failing):
    from agent_containers.installer_readiness import inspect_toolchain

    monkeypatch.setattr("agent_containers.installer_readiness._is_windows", lambda: False)
    monkeypatch.setenv("FAKE_DRIVER_CAPS", json.dumps(caps))
    failures = inspect_toolchain(
        driver_config(), command_finder=lambda name: None,
    )
    assert any("OpenSSH" in f for f in failures) is failing
    assert not any("docker" in f for f in failures)


@posix_only
def test_readiness_reports_unlaunchable_driver_even_with_openssh(fake):
    from agent_containers.installer_readiness import inspect_toolchain

    script = fake.root / "not-a-program"
    script.write_text("not a program\n")
    script.chmod(0o755)
    (fake.drivers / "broken-exec.json").write_text(json.dumps(
        {"schema_version": 1, "name": "broken-exec", "command": [str(script)]},
    ))
    failures = inspect_toolchain(
        driver_config(driver="broken-exec"), command_finder=lambda name: f"/usr/bin/{name}",
    )
    assert any("runtime_unavailable" in f for f in failures)


@posix_only
def test_runtime_proxy_serves_bridge_hosting_checks(fake, no_docker, tmp_path, monkeypatch, capsys):
    config = _write_config(tmp_path, monkeypatch)
    fleet.reconcile_up(config, "example")
    member_id = {c.name: c for c in lifecycle.list_containers(config)}["example-1"].container_id
    runtime_drivers.clear_bindings()  # agent-bridge runs the proxy in a fresh process
    assert cli.main(["runtime-proxy", "inspect", member_id]) == 0
    [document] = json.loads(capsys.readouterr().out)
    assert (document["Id"], document["State"]["Running"]) == (member_id, True)
    runtime_drivers.clear_bindings()
    assert cli.main(["runtime-proxy", "exec", "-u", "vscode", member_id,
                     "/bin/sh", "-c", "echo policy-ok"]) == 0
    assert capsys.readouterr().out == "policy-ok\n"
    assert cli.main(["runtime-proxy", "inspect", "plain-1"]) == 1  # never a Docker fallback


@pytest.mark.parametrize("denied", [False, True])
def test_runtime_proxy_delivers_portable_policy_archive_to_real_driver_process(
    fake, no_docker, tmp_path, monkeypatch, capsys, denied,
):
    import base64
    import io

    from repository_policy.receiving import build_receiving_policy_archive

    config = _write_config(tmp_path, monkeypatch)
    fleet.reconcile_up(config, "example")
    member_id = {c.name: c for c in lifecycle.list_containers(config)}["example-1"].container_id
    repo = tmp_path / "source"
    (repo / ".git").mkdir(parents=True)
    policy = repo / ".agent-worktrees" / "config.yaml"
    policy.parent.mkdir()
    policy.write_text(f"expose_agent: {'false' if denied else 'true'}\n", encoding="utf-8")
    reader, payload = build_receiving_policy_archive()
    context = json.dumps({
        "schema": "agent-bridge.receiving-policy", "version": 1, "marketplaceId": "",
    })
    encoded = base64.b64encode(payload).decode("ascii")
    monkeypatch.setattr(sys, "stdin", io.StringIO(encoded))
    runtime_drivers.clear_bindings()
    rc = cli.main([
        "runtime-proxy", "--fleet", "example", "exec", "-i", "-u", "vscode", member_id,
        sys.executable, "-I", "-S", "-c", reader, str(repo), context, "resume", "base64",
    ])
    assert rc == (3 if denied else 0)
    answer = json.loads(capsys.readouterr().out)
    if denied:
        assert "expose_agent: false" in answer["error"]
    else:
        assert answer["allowed"] is True
    executions = [call for call in fake.state()["calls"] if call["verb"] == "exec"]
    assert len(executions) == 1 and executions[0]["request"]["stdin"] == encoded


def test_session_host_state_reports_recreated_incarnation_missing(
    fake, no_docker, tmp_path, monkeypatch, capsys,
):
    config = _write_config(tmp_path, monkeypatch)
    fleet.reconcile_up(config, "example")
    old = {c.name: c for c in lifecycle.list_containers(config)}["example-1"].container_id
    lifecycle.remove_container("example-1", force=True)
    fleet.reconcile_up(config, "example")
    runtime_drivers.clear_bindings()
    assert cli.main(["session-host-state", "example-1", "--expected-instance", old]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "missing"


@posix_only
def test_copilot_login_operations_route_through_driver(fake, no_docker, monkeypatch):
    from agent_containers import copilot_login

    member = _provisioned(driver_config(size=1))
    read = copilot_login._run(
        ["docker", "exec", "-u", "vscode", member.container_id, "bash", "-lc", "echo config"],
        capture_output=True, text=True, timeout=30,
    )
    assert (read.returncode, read.stdout) == (0, "config\n")
    seen = []
    monkeypatch.setattr(copilot_login.subprocess, "run", lambda argv, **kw: seen.append(argv)
                        or subprocess.CompletedProcess(argv, 0))
    copilot_login._run(
        ["docker", "exec", "-it", "-u", "vscode", member.container_id, "copilot", "login"],
        interactive=True,
    )
    request = json.loads(seen[0][seen[0].index("--request") + 1])
    assert seen[0][2] == "exec-stream"
    assert (request["id"], request["argv"]) == (member.container_id, ["copilot", "login"])


def _two_driver_config(tmp_path, monkeypatch, fake, slow_command):
    (fake.drivers / "other-driver.json").write_text(json.dumps({
        "schema_version": 1, "name": "other-driver",
        "command": [sys.executable, str(FAKE), *slow_command],
    }))
    path = tmp_path / "containers.yaml"
    path.write_text(yaml.safe_dump({"fleets": {
        "example": {"driver": "example-driver", "size": 1, "relay_enabled": False},
        "slow": {"driver": "other-driver", "size": 1, "relay_enabled": False},
    }}))
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(path))
    return load_config(strict=True)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_hung_driver_does_not_stall_a_healthy_member(fake, no_docker, tmp_path, monkeypatch, capsys):
    import time

    slow_root = tmp_path / "slow-runtime"
    config = _two_driver_config(
        tmp_path, monkeypatch, fake, ["--root", str(slow_root), "--hang", "list"],
    )
    fleet.reconcile_up(config, "example")
    member_id = runtime_drivers.owner_of("example-1").driver.list_members()[0].container_id

    # A fresh runtime-proxy process (agent-bridge allows it 30s and passes the
    # venue's fleet) asks only the member's own driver.
    runtime_drivers.clear_bindings()
    started = time.monotonic()
    assert cli.main(["runtime-proxy", "--fleet", "example", "inspect", member_id]) == 0
    assert time.monotonic() - started < 10
    assert json.loads(capsys.readouterr().out)[0]["Id"] == member_id
    assert not (slow_root / "hung-descendant.pid").exists()

    # Inventory is bounded by one overall deadline, not one per fleet, and the
    # hung driver's whole process tree -- including a descendant holding its
    # output pipes -- is retired at that deadline.
    runtime_drivers.clear_bindings()
    started = time.monotonic()
    listed = runtime_drivers.list_driver_members(config, timeout=2)
    assert time.monotonic() - started < 8
    assert [m.name for m in listed] == ["example-1"]
    descendant = int((slow_root / "hung-descendant.pid").read_text())
    for _ in range(50):
        if not _alive(descendant):
            break
        time.sleep(0.1)
    assert not _alive(descendant)


def test_delayed_duplicate_id_is_ambiguous_without_a_fleet(fake, no_docker, tmp_path, monkeypatch, capsys):
    slow_root = tmp_path / "slow-runtime"
    config = _two_driver_config(
        tmp_path, monkeypatch, fake, ["--root", str(slow_root), "--delay", "list", "1"],
    )
    fleet.reconcile_up(config, "example")
    fleet.reconcile_up(config, "slow")
    ids = {m.name: m.container_id for m in lifecycle.list_containers(config)}
    assert ids["example-1"] == ids["slow-1"]  # independent runtimes, same native id

    runtime_drivers.clear_bindings()
    assert cli.main(["runtime-proxy", "inspect", ids["example-1"]]) == 1
    assert "more than one runtime driver" in capsys.readouterr().err
    runtime_drivers.clear_bindings()
    assert cli.main(["runtime-proxy", "--fleet", "slow", "inspect", ids["slow-1"]]) == 0
    document = json.loads(capsys.readouterr().out)[0]
    assert document["Config"]["Labels"]["agent-containers.fleet"] == "slow"


def test_discovery_deadline_never_cancels_other_calls_on_a_driver(fake, no_docker, tmp_path, monkeypatch):
    import threading

    slow_root = tmp_path / "slow-runtime"
    config = _two_driver_config(
        tmp_path, monkeypatch, fake, ["--root", str(slow_root), "--hang", "list"],
    )
    fleet.reconcile_up(config, "example")
    driver = runtime_drivers.owner_of("example-1").driver
    outcome = {}

    def long_exec():
        outcome["result"] = driver.exec("example-1", [sys.executable, "-c",
                                        "import time; time.sleep(2); print('finished')"],
                                        timeout=30)

    worker = threading.Thread(target=long_exec)
    worker.start()
    runtime_drivers.list_driver_members(config, timeout=1)  # gives up on "slow"
    worker.join(30)
    assert outcome["result"].exit_code == 0
    assert outcome["result"].stdout.strip() == "finished"


def test_labelled_row_of_unavailable_driver_is_never_managed_by_docker(fake, monkeypatch):
    from agent_containers.lifecycle_ownership import DRIVER_REASON, lifecycle_blocker

    config = ContainersConfig(fleets={"example": driver_fleet()})
    labels = {"agent-containers.fleet": "example", "agent-containers.driver": "example-driver"}
    # Labels alone (driver unavailable, nothing discovered) never admit Docker.
    assert lifecycle_blocker(config, "example-1", labels) == DRIVER_REASON
    monkeypatch.delenv("FAKE_DRIVER_FAIL", raising=False)
    _docker_rows(monkeypatch)
    fleet.reconcile_up(config, "example")
    member = {m.name: m for m in lifecycle.list_containers(config)}["example-1"]  # now routed
    # Only the exact reported incarnation is admitted, never the name alone.
    assert lifecycle_blocker(config, "example-1", labels, member.container_id) is None
    assert lifecycle_blocker(config, "example-1", labels) == DRIVER_REASON
    assert lifecycle_blocker(config, "example-1", labels, "b" * 64) == DRIVER_REASON


@posix_only
def test_named_launch_with_duplicate_ids_keeps_its_fleet_route(fake, no_docker, tmp_path, monkeypatch):
    from agent_containers import hosting_policy

    slow_root = tmp_path / "slow-runtime"
    config = _two_driver_config(tmp_path, monkeypatch, fake, ["--root", str(slow_root)])
    fleet.reconcile_up(config, "example")
    fleet.reconcile_up(config, "slow")
    target = resolver.resolve_live_exec_target("example-1", config=config)
    raw_id = runtime_drivers.owner_of("example-1").driver.inspect("example-1")["Id"]
    with pytest.raises(RuntimeError, match="more than one runtime driver"):
        runtime_drivers.owner_of(raw_id)  # the bare id is shared by both runtimes
    assert target.container_id == f"example-1.{raw_id}"

    # The hosting check (and every other exec addressed by the target) reaches
    # the selected member, pinned to its incarnation.
    done = hosting_policy._run(target, "echo hosted")
    assert done.stdout == "hosted\n"
    example_exec = [c for c in fake.state()["calls"] if c["verb"] == "exec"][-1]["request"]
    assert (example_exec["name"], example_exec["id"], example_exec["fleet"]) == (
        "example-1", raw_id, "example",
    )
    slow_calls = json.loads((slow_root / "state.json").read_text())["calls"]
    assert not any(c["verb"] == "exec" for c in slow_calls)


def test_pinned_session_host_prepare_with_duplicate_ids(fake, no_docker, tmp_path, monkeypatch, capsys):
    from ssh_manager import SSHConfig

    from agent_containers import hosting_policy
    from agent_containers.session_host_context import cleanup_user

    monkeypatch.setenv("FAKE_DRIVER_CAPS", json.dumps({"ssh_transport": True}))
    slow_root = tmp_path / "slow-runtime"
    path = tmp_path / "containers.yaml"
    (fake.drivers / "other-driver.json").write_text(json.dumps({
        "schema_version": 1, "name": "other-driver",
        "command": [sys.executable, str(FAKE), "--root", str(slow_root)],
    }))
    path.write_text(yaml.safe_dump({"fleets": {
        name: {"driver": driver, "size": 1, "relay_enabled": False, "copilot_auth_mode": "legacy",
               "forward_gh_token": False}
        for name, driver in (("example", "example-driver"), ("slow", "other-driver"))
    }}))
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(path))
    config = load_config(strict=True)
    fleet.reconcile_up(config, "example")
    fleet.reconcile_up(config, "slow")
    raw_id = runtime_drivers.owner_of("example-1").driver.inspect("example-1")["Id"]
    handle = f"example-1.{raw_id}"

    seen = {}
    monkeypatch.setattr(hosting_policy, "require_container_checkout",
                        lambda target, user, workspace: seen.setdefault("checkout", target))
    monkeypatch.setattr(cli, "prepare_ssh_config",
                        lambda target, user: seen.setdefault("ssh", target) and SSHConfig(host_alias="fixture"))
    monkeypatch.setattr(cli, "cleanup_remote_envs", lambda target, user: seen.setdefault("cleanup", target))
    monkeypatch.setattr(cli, "container_environment",
                        lambda target, user: seen.setdefault("environment", target) and {})
    monkeypatch.setattr(cli, "write_remote_env",
                        lambda target, user, values: seen.setdefault("staged", target) and "/launch/1.env")

    runtime_drivers.clear_bindings()  # agent-bridge runs prepare in a fresh process
    assert cli.main(["session-host-prepare", "example-1", "--expected-instance", raw_id]) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["execution_instance"] == raw_id
    assert seen == {"checkout": handle, "ssh": handle, "cleanup": handle,
                    "environment": handle, "staged": handle}
    # Pinned cleanup resolves the same handle; once every runtime is
    # enumerated, the raw id alone is ambiguous.
    lifecycle.list_containers(config)
    assert cleanup_user(raw_id, "vscode", handle) == "vscode"
    with pytest.raises(RuntimeError, match="more than one runtime driver"):
        cleanup_user(raw_id, "vscode")


def _capture_docker(monkeypatch, documents):
    """Answer docker inspect from ``documents``; record every other argv unexecuted."""
    issued = []

    def run(argv, **kwargs):
        assert argv[0] == "docker"
        if argv[1] == "inspect":
            document = documents.get(argv[-1])
            if document is None:
                return subprocess.CompletedProcess(argv, 1, "", f"Error: No such object: {argv[-1]}")
            return subprocess.CompletedProcess(argv, 0, json.dumps([document]), "")
        issued.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(lifecycle.subprocess, "run", run)
    return issued


@pytest.mark.parametrize("labels", [
    {"agent-containers.fleet": "example"},                # claims the driver fleet
    {"agent-containers.fleet": "example", "agent-containers.driver": "example-driver"},
    {},                                                   # deterministic driver-fleet name
    {"agent-containers.fleet": "plain"},                  # only the name collides
])
def test_same_name_docker_incarnation_is_never_managed_through_docker(
    fake, tmp_path, monkeypatch, labels,
):
    path = tmp_path / "containers.yaml"
    path.write_text(yaml.safe_dump({"fleets": {
        "example": {"driver": "example-driver", "size": 1, "relay_enabled": False},
        "plain": {"image": "example/image:1"},
    }}))
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(path))
    config = load_config(strict=True)
    real_docker = lifecycle._docker
    _docker_rows(monkeypatch)
    fleet.reconcile_up(config, "example")
    lifecycle.list_containers(config)  # the driver now owns example-1 at incarnation A
    monkeypatch.setattr(lifecycle, "_docker", real_docker)  # the real Docker boundary
    docker_id = "b" * 64
    plain_id = "c" * 64
    issued = _capture_docker(monkeypatch, {
        docker_id: {"Id": docker_id, "Name": "/example-1", "Config": {"Labels": labels},
                    "State": {"Status": "running"}},
        plain_id: {"Id": plain_id, "Name": "/plain-1",
                   "Config": {"Labels": {"agent-containers.fleet": "plain"}},
                   "State": {"Status": "running"}},
    })

    for operation in (
        lambda: lifecycle.remove_container(docker_id, force=True),
        lambda: lifecycle.stop_container(docker_id),
        lambda: lifecycle.start_container(docker_id),
    ):
        with pytest.raises(RuntimeError, match="only its driver may manage it"):
            operation()
    assert issued == []  # no docker rm/stop/start was ever issued for B
    assert lifecycle.inspect_state("example-1") == "running"  # A untouched

    # Control: an unrelated Docker member keeps its pinned Docker lifecycle.
    lifecycle.remove_container(plain_id, force=True)
    assert issued == [["docker", "rm", "-f", plain_id]]


def test_targeted_recreation_with_ids_shared_across_driver_fleets(fake, no_docker, tmp_path, monkeypatch):
    slow_root = tmp_path / "slow-runtime"
    config = _two_driver_config(tmp_path, monkeypatch, fake, ["--root", str(slow_root)])
    fleet.reconcile_up(config, "example")
    fleet.reconcile_up(config, "slow")
    members = {m.name: m for m in lifecycle.list_containers(config)}
    shared = members["example-1"].container_id
    assert members["slow-1"].container_id == shared

    result = fleet.recreate_member(config, "example-1", expected_container_id=shared)
    assert result["old_container_id"] == shared
    assert result["new_container_id"] != shared
    removed = [c["request"] for c in fake.state()["calls"] if c["verb"] == "remove"]
    assert removed == [{**removed[0], "name": "example-1", "id": shared, "force": True}]
    slow_calls = json.loads((slow_root / "state.json").read_text())["calls"]
    assert not any(c["verb"] == "remove" for c in slow_calls)
    assert runtime_drivers.owner_of("slow-1").driver.state("slow-1") == "running"


def test_acp_launch_refuses_a_member_recreated_after_validation(fake, no_docker, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DRIVER_CAPS", json.dumps({"owns_relay": True}))
    config = _write_config(tmp_path, monkeypatch, relay_enabled=True)
    fleet.reconcile_up(config, "example")
    real_resolve = cli.resolve_live_exec_target

    def resolve_then_recreate(name, **kwargs):
        target = real_resolve(name, **kwargs)  # validated incarnation A
        lifecycle.remove_container(name, force=True)
        fleet.reconcile_up(load_config(strict=True), "example")  # replacement B
        return target

    launched = []
    monkeypatch.setattr(cli, "resolve_live_exec_target", resolve_then_recreate)
    monkeypatch.setattr(cli.hosting_policy, "require_container_hosting", lambda *a, **k: None)
    monkeypatch.setattr(cli, "host_gh_token", lambda: "gh-token")
    monkeypatch.setattr(cli, "_exec_stdio", lambda argv, env: launched.append(argv) or 0)
    args = SimpleNamespace(name="example-1", stdio=True, force=False, copilot_args=None)
    with pytest.raises(DriverError) as raised:
        cli._cmd_exec(args)
    assert raised.value.code == "conflict"  # the replacement never receives the launch
    assert launched == []
    staged = [c for c in fake.state()["calls"] if c["verb"] == "exec"]
    assert staged and all(c["request"]["id"] != runtime_drivers.owner_of("example-1").driver
                          .inspect("example-1")["Id"] for c in staged)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_invalid_utf8_driver_output_is_a_typed_error(fake, no_docker, tmp_path, monkeypatch, stream):
    import time

    config = _two_driver_config(tmp_path, monkeypatch, fake,
                                ["--root", str(tmp_path / "slow"), "--bad-bytes", "list", stream])
    fleet.reconcile_up(config, "example")
    driver = runtime_drivers.driver_for_fleet(config, "slow", config.fleets["slow"])
    with pytest.raises(DriverError) as raised:
        driver.list_members()
    assert raised.value.code in {"protocol_error", "runtime_unavailable"}
    runtime_drivers.clear_bindings()
    started = time.monotonic()
    listed = runtime_drivers.list_driver_members(config, timeout=30)
    assert time.monotonic() - started < 10  # isolated immediately, not at the deadline
    assert [m.name for m in listed] == ["example-1"]


def test_rejected_listing_commits_no_routing(fake, no_docker, tmp_path, monkeypatch):
    slow_root = tmp_path / "slow"
    config = _two_driver_config(tmp_path, monkeypatch, fake, ["--root", str(slow_root)])
    fleet.reconcile_up(config, "example")
    fleet.reconcile_up(config, "slow")
    (fake.drivers / "other-driver.json").write_text(json.dumps({
        "schema_version": 1, "name": "other-driver",
        "command": [sys.executable, str(FAKE), "--root", str(slow_root), "--invalid-tail"],
    }))
    runtime_drivers.clear_bindings()
    listed = runtime_drivers.list_driver_members(config)
    assert [m.name for m in listed] == ["example-1"]
    assert runtime_drivers.owner_of("slow-1") is None  # valid row of the rejected listing
    shared = listed[0].container_id
    assert runtime_drivers.owner_of(shared).name == "example-1"  # not made ambiguous


def test_installer_readiness_reports_a_driver_that_cannot_list(fake, no_docker, tmp_path, monkeypatch, capsys):
    _write_config(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_DRIVER_FAIL", "list:runtime_unavailable")
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    assert cli.main(["installer-readiness"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "failed"
    assert "runtime_unavailable" in result["detail"]


def test_session_host_prepare_ignores_an_unrelated_hung_driver(
    fake, no_docker, tmp_path, monkeypatch, capsys,
):
    import time

    from ssh_manager import SSHConfig

    from agent_containers import hosting_policy

    monkeypatch.setenv("FAKE_DRIVER_CAPS", json.dumps({"ssh_transport": True}))
    (fake.drivers / "other-driver.json").write_text(json.dumps({
        "schema_version": 1, "name": "other-driver",
        "command": [sys.executable, str(FAKE), "--root", str(tmp_path / "slow"), "--hang", "list"],
    }))
    path = tmp_path / "containers.yaml"
    path.write_text(yaml.safe_dump({"fleets": {
        name: {"driver": driver, "size": 1, "relay_enabled": False, "copilot_auth_mode": "legacy",
               "forward_gh_token": False}
        for name, driver in (("example", "example-driver"), ("slow", "other-driver"))
    }}))
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(path))
    fleet.reconcile_up(load_config(strict=True), "example")
    monkeypatch.setattr(hosting_policy, "require_container_checkout", lambda *a: None)
    monkeypatch.setattr(cli, "prepare_ssh_config", lambda *a: SSHConfig(host_alias="fixture"))
    monkeypatch.setattr(cli, "cleanup_remote_envs", lambda *a: None)
    monkeypatch.setattr(cli, "container_environment", lambda *a: {})
    monkeypatch.setattr(cli, "write_remote_env", lambda *a: "/launch/1.env")
    runtime_drivers.clear_bindings()
    started = time.monotonic()
    assert cli.main(["session-host-prepare", "example-1"]) == 0
    assert time.monotonic() - started < 10
    assert json.loads(capsys.readouterr().out)["name"] == "example-1"


def test_fresh_scoped_lookup_refuses_a_docker_name_collision(fake, tmp_path, monkeypatch, capsys):
    config = _write_config(tmp_path, monkeypatch)
    real_docker = lifecycle._docker
    _docker_rows(monkeypatch)
    fleet.reconcile_up(config, "example")
    driver_id = runtime_drivers.owner_of("example-1").driver.inspect("example-1")["Id"]
    monkeypatch.setattr(lifecycle, "_docker", real_docker)
    docker_answers = {"id": "d" * 64}

    def docker_run(argv, **kwargs):
        assert argv[:2] == ["docker", "inspect"], argv  # the only, bounded Docker probe
        return subprocess.CompletedProcess(argv, 0, docker_answers["id"] + "\n", "")

    monkeypatch.setattr(lifecycle.subprocess, "run", docker_run)
    monkeypatch.setattr(lifecycle, "list_containers", lambda *a, **k: pytest.fail("full inventory"))

    runtime_drivers.clear_bindings()  # a fresh exec / Session Host process
    with pytest.raises(RuntimeError, match="distinct Docker container"):
        resolver.resolve_live_exec_target("example-1", config=load_config(strict=True))
    runtime_drivers.clear_bindings()
    assert cli.main(["session-host-prepare", "example-1"]) == 1
    assert "distinct Docker container" in capsys.readouterr().err

    # The same container seen through Docker (same id) is not a collision.
    docker_answers["id"] = driver_id
    runtime_drivers.clear_bindings()
    target = resolver.resolve_live_exec_target("example-1", config=load_config(strict=True))
    assert target.container_id == f"example-1.{driver_id}"

    # Same process, no clear_bindings(): a collision blocks, and resolving it
    # (the distinct Docker container goes away) unblocks on the next probe.
    docker_answers["id"] = "d" * 64
    with pytest.raises(RuntimeError, match="distinct Docker container"):
        resolver.resolve_live_exec_target("example-1", config=load_config(strict=True))
    docker_answers["id"] = driver_id
    target = resolver.resolve_live_exec_target("example-1", config=load_config(strict=True))
    assert target.container_id == f"example-1.{driver_id}"


def test_hung_selected_driver_fails_within_the_targeted_budget(fake, no_docker, tmp_path, monkeypatch, capsys):
    import time

    config = _write_config(tmp_path, monkeypatch)
    fleet.reconcile_up(config, "example")
    (fake.drivers / "example-driver.json").write_text(json.dumps({
        "schema_version": 1, "name": "example-driver",
        "command": [sys.executable, str(FAKE), "--hang", "list"],
    }))
    monkeypatch.setitem(runtime_drivers.scoped_member.__kwdefaults__, "timeout", 2.0)
    monkeypatch.setattr(lifecycle, "list_containers", lambda *a, **k: pytest.fail("full inventory"))
    runtime_drivers.clear_bindings()
    started = time.monotonic()
    assert cli.main(["session-host-prepare", "example-1"]) == 1
    assert time.monotonic() - started < 8
    assert "Runtime driver for 'example-1' is unavailable" in capsys.readouterr().err


# -- plugin-shipped fleet fragments ------------------------------------------

SHIPPED_DRIVER_FRAGMENT = """\
fleets:
  example:
    driver: example-driver
    driver_options:
      profile: shipped
      nested: {a: 1}
    size: 1
    workspace_folder: /workspaces/example
    relay_enabled: false
"""


@pytest.fixture
def shipped_driver_fleet(fake, tmp_path, monkeypatch):
    """A plugin fragment ships ``example``; returns an operator-config writer."""
    from agent_containers import config as config_mod
    from agent_containers import plugin_config

    root = tmp_path / "plugins" / "example-harness"
    root.mkdir(parents=True)
    (root / "containers.yaml").write_text(SHIPPED_DRIVER_FRAGMENT, encoding="utf-8")
    (root / "plugin.json").write_text(json.dumps({
        "name": "example-harness", plugin_config.MANIFEST_FIELD: "containers.yaml",
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config_mod, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(config_mod, "_knowledge_overlay_config", lambda: None)
    monkeypatch.setattr(plugin_config, "_active_plugin_roots",
                        lambda *_: [("example-harness@example", [root])])

    def operator(body: str):
        path = tmp_path / "operator" / "containers.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(path))
        return load_config(strict=True)

    return operator


def test_shipped_driver_fleet_with_operator_override_routes_through_its_driver(
    fake, no_docker, shipped_driver_fleet,
):
    config = shipped_driver_fleet(
        "schema_version: 2\nfleets:\n  example:\n    driver_options:\n      profile: operator\n"
    )
    selected = config.fleets["example"]
    assert config.fleet_source("example") == "plugin:example-harness@example"
    assert (selected.driver, selected.driver_options) == (
        "example-driver", {"profile": "operator", "nested": {"a": 1}},
    )
    assert fleet.reconcile_up(config, "example").created == ["example-1"]
    provision = [c["request"] for c in fake.state()["calls"] if c["verb"] == "provision"]
    assert provision[0]["driverOptions"] == {"profile": "operator", "nested": {"a": 1}}
    runtime_drivers.clear_bindings()
    [member] = lifecycle.list_containers(config)
    assert (member.name, member.driver) == ("example-1", "example-driver")
    assert runtime_drivers.owner_of("example-1").driver.name == "example-driver"


def test_operator_docker_key_on_a_shipped_driver_fleet_is_refused(shipped_driver_fleet):
    with pytest.raises(RuntimeError, match="driver fleets cannot set image"):
        shipped_driver_fleet(
            "schema_version: 2\nfleets:\n  example:\n    image: example/image:1\n"
        )


@pytest.mark.parametrize("entry", ["example:\n", "example: {}\n", "example: null\n"])
def test_empty_operator_entry_removes_shipped_driver_routing(
    fake, no_docker, shipped_driver_fleet, entry,
):
    config = shipped_driver_fleet("schema_version: 2\nfleets:\n  " + entry)
    assert "example" not in config.fleets
    assert runtime_drivers.list_driver_members(config) == []
    assert runtime_drivers.owner_of("example-1") is None
    assert [c for c in fake.state()["calls"] if c["verb"] == "list"] == []
    with pytest.raises(RuntimeError, match="'example' is not defined"):
        fleet.reconcile_up(config, "example")


def _docker_recorder(monkeypatch):
    """Real Docker path with an empty runtime; records every docker argv."""
    issued = []

    def run(argv, **kwargs):
        issued.append(argv)
        if argv[1] == "inspect":
            return subprocess.CompletedProcess(argv, 1, "", f"Error: No such object: {argv[-1]}")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(lifecycle, "_docker", _REAL_DOCKER)
    monkeypatch.setattr(lifecycle.subprocess, "run", run)
    return issued


def _assert_retired(issued, handles, fake):
    driver_calls = len(fake.state()["calls"])
    for handle in handles:
        assert runtime_drivers.owner_of(handle) is None
        with pytest.raises(lifecycle.ContainerNotFoundError):
            lifecycle.inspect_container(handle)
        assert issued[-1] == ["docker", "inspect", handle]
        lifecycle._docker(["exec", handle, "true"])
        assert issued[-1] == ["docker", "exec", handle, "true"]
    assert len(fake.state()["calls"]) == driver_calls  # the retired driver is never asked


def test_configuration_reload_reconciles_driver_routing_without_clearing(
    fake, no_docker, tmp_path, monkeypatch,
):
    # One process, no clear_bindings(): reloads must reconcile routing.
    config = _write_config(tmp_path, monkeypatch)
    fleet.reconcile_up(config, "example")
    lifecycle.list_containers(config)
    old_id = runtime_drivers.owner_of("example-1").driver.inspect("example-1")["Id"]

    # The fleet is removed: normal inventory after the reload retires its
    # name, raw id and qualified handle, and later commands reach Docker only.
    path = tmp_path / "containers.yaml"
    path.write_text(yaml.safe_dump({"fleets": {}}))
    reloaded = load_config(strict=True)
    refuse = lifecycle._docker
    issued = _docker_recorder(monkeypatch)
    assert lifecycle.list_containers(reloaded) == []
    _assert_retired(issued, ["example-1", old_id, f"example-1.{old_id}"], fake)
    monkeypatch.setattr(lifecycle, "_docker", refuse)
    monkeypatch.setattr(lifecycle.subprocess, "run", subprocess.run)

    # The fleet returns on a replacement driver: names are not ambiguous.
    (fake.drivers / "replacement-driver.json").write_text(json.dumps({
        "schema_version": 1, "name": "replacement-driver",
        "command": [sys.executable, str(FAKE)],
    }))
    path.write_text(yaml.safe_dump({"fleets": {"example": {
        "driver": "replacement-driver", "size": 1, "relay_enabled": False,
    }}}))
    replaced = load_config(strict=True)
    lifecycle.list_containers(replaced)
    route = runtime_drivers.owner_of("example-1")
    assert route.driver.name == "replacement-driver"

    # A member the driver stops reporting stops routing by name, while its
    # previous incarnation id stays pinned (and is refused by the driver).
    pinned = runtime_drivers.owner_of(f"example-1.{old_id}")
    lifecycle.remove_container("example-1", force=True)
    runtime_drivers.list_driver_members(replaced)
    assert runtime_drivers.owner_of("example-1") is None
    assert pinned is not None and runtime_drivers.owner_of(f"example-1.{old_id}") is not None
    with pytest.raises(lifecycle.ContainerNotFoundError):
        lifecycle.inspect_container(f"example-1.{old_id}")


@pytest.fixture
def recreated_after_validation(fake, no_docker, tmp_path, monkeypatch):
    """A launch validated incarnation A; the member is then recreated as B."""
    monkeypatch.setenv("FAKE_DRIVER_CAPS", json.dumps({"ssh_transport": True}))
    config = _write_config(tmp_path, monkeypatch, relay_enabled=False)
    fleet.reconcile_up(config, "example")
    (validated,) = lifecycle.list_containers(config)
    target = SimpleNamespace(
        name="example-1", container_id=runtime_drivers.route_handle(validated),
        config=config, fleet=config.fleets["example"], user="vscode", actual_profile="trusted",
        workspace_folder="/workspaces/example",
    )
    lifecycle.remove_container("example-1", force=True)
    fleet.reconcile_up(config, "example")
    (replacement,) = lifecycle.list_containers(config)
    assert replacement.container_id != validated.container_id
    return SimpleNamespace(target=target, old=validated.container_id,
                           new=replacement.container_id)


def _assert_pinned_to_validated(state, seen):
    route = runtime_drivers.owner_of("example-1")
    seen.append(route.instance_id)
    # Credential staging / SSH preparation by name reach only incarnation A,
    # which the driver refuses rather than serving replacement B.
    with pytest.raises(runtime_drivers.DriverError):
        runtime_drivers.run_routed(["docker", "exec", "-i", "example-1", "true"])
    proxy = runtime_drivers.stream_routed(["docker", "exec", "-i", "example-1", "sshd"])
    assert state.old in " ".join(proxy) and state.new not in " ".join(proxy)


def test_attached_copilot_launch_stays_on_the_validated_incarnation(
    recreated_after_validation, monkeypatch,
):
    import ssh_manager
    import venue_copilot
    from agent_containers import config as config_mod, copilot_venue, hosting_policy
    from agent_containers import ssh_transport

    state = recreated_after_validation
    seen = []

    class Lock:
        def __init__(self, *a, **k): pass
        def acquire(self, force=False): pass
        def release(self): pass

    def prepare(name, user):
        _assert_pinned_to_validated(state, seen)
        raise RuntimeError("stop after preparation")

    monkeypatch.setattr(config_mod, "load_config", lambda *a, **k: state.target.config)
    monkeypatch.setattr(resolver, "resolve_live_exec_target", lambda name, config=None: state.target)
    monkeypatch.setattr(venue_copilot, "live_session_for", lambda scope: None)
    monkeypatch.setattr(venue_copilot, "resolve_daemon_port", lambda: None)
    monkeypatch.setattr(hosting_policy, "require_container_hosting", lambda *a, **k: None)
    monkeypatch.setattr(ssh_manager, "TargetLock", Lock)
    monkeypatch.setattr(ssh_transport, "prepare_ssh_config", prepare)
    args = SimpleNamespace(name="example-1", worktree_id=None, no_relay=True, force=False,
                           ref_files=None, ttl_seconds=None, stop=False, detach=False)
    with pytest.raises(RuntimeError, match="stop after preparation"):
        copilot_venue.cmd_copilot(
            args, require_live_relay_port=lambda: 0, relay_healthy=lambda port: True,
            busy_exit=75, creation_flags=lambda: 0,
        )
    assert seen == [state.old]
    assert runtime_drivers.owner_of("example-1").instance_id is None  # unpinned


def test_detached_copilot_launch_stays_on_the_validated_incarnation(
    recreated_after_validation, monkeypatch, capsys,
):
    import ssh_manager
    import venue_copilot
    from agent_containers import copilot_detach, forward_keeper, hosting_policy

    state = recreated_after_validation
    seen = []

    class Lock:
        def __init__(self, *a, **k): pass
        def acquire(self, force=False): pass
        def release(self): pass

    def launch_env(args, target, **kwargs):
        _assert_pinned_to_validated(state, seen)
        raise RuntimeError("stop after preparation")

    monkeypatch.setattr(copilot_detach, "_resolve_target", lambda args: state.target)
    monkeypatch.setattr(copilot_detach, "plan_for", lambda args, target: {
        "scope_id": "anchor@example-1", "venue": {}, "identity": "anchor"})
    monkeypatch.setattr(copilot_detach, "with_supervisor", lambda venue: venue)
    monkeypatch.setattr(copilot_detach, "read_seed", lambda args: None)
    monkeypatch.setattr(copilot_detach, "upload_for", lambda refs, scope: None)
    monkeypatch.setattr(copilot_detach, "_launch_env", launch_env)
    monkeypatch.setattr(venue_copilot, "live_session_for", lambda scope: None)
    monkeypatch.setattr(forward_keeper, "read_state", lambda name: {})
    monkeypatch.setattr(hosting_policy, "require_container_hosting", lambda *a, **k: None)
    monkeypatch.setattr(ssh_manager, "TargetLock", Lock)
    args = SimpleNamespace(name="example-1", worktree_id=None, force=False, dry_run=False)
    assert copilot_detach.cmd_detach(
        args, require_live_relay_port=lambda: 0, relay_healthy=lambda port: True,
    ) != 0
    assert "stop after preparation" in capsys.readouterr().out
    assert seen == [state.old]
    assert runtime_drivers.owner_of("example-1").instance_id is None  # unpinned


def test_clearing_the_last_shipped_driver_fleet_retires_its_routes_on_inventory(
    fake, no_docker, shipped_driver_fleet, monkeypatch,
):
    config = shipped_driver_fleet("schema_version: 2\nfleets: {}\n")
    fleet.reconcile_up(config, "example")
    [member] = lifecycle.list_containers(config)
    handles = ["example-1", member.container_id, runtime_drivers.route_handle(member)]
    assert all(runtime_drivers.owner_of(h) is not None for h in handles)

    cleared = shipped_driver_fleet("schema_version: 2\nfleets:\n  example:\n")
    assert not any(f.driver for f in cleared.fleets.values())
    issued = _docker_recorder(monkeypatch)
    assert lifecycle.list_containers(cleared) == []
    _assert_retired(issued, handles, fake)


def test_namespace_resolve_of_absent_driver_member_never_queries_other_fleets(
    fake, no_docker, tmp_path, monkeypatch,
):
    import time

    slow_root = tmp_path / "slow-runtime"
    config = _two_driver_config(
        tmp_path, monkeypatch, fake, ["--root", str(slow_root), "--hang", "list"],
    )
    fleet.reconcile_up(config, "example")
    runtime_drivers.clear_bindings()

    def no_full_inventory(*args, **kwargs):
        raise AssertionError("full inventory consulted after a scoped absence")

    monkeypatch.setattr(resolver, "list_containers", no_full_inventory)
    monkeypatch.setattr(resolver, "get_container", no_full_inventory)
    monkeypatch.setattr(lifecycle, "list_containers", no_full_inventory)
    started = time.monotonic()
    with pytest.raises(KeyError, match="example-99.*not found in its runtime-driver fleet"):
        asyncio.run(resolver.ContainerResolver().resolve_spec("example-99"))
    assert time.monotonic() - started < 15
    assert not slow_root.exists()  # the unrelated driver was never invoked


@pytest.mark.parametrize("retained_runtime", [True, False])
def test_session_host_cleanup_after_fleet_removal_never_reaches_a_same_id_member(
    fake, no_docker, tmp_path, monkeypatch, capsys, retained_runtime,
):
    other_root = tmp_path / "other-runtime"
    config = _two_driver_config(tmp_path, monkeypatch, fake, ["--root", str(other_root)])
    fleet.reconcile_up(config, "example")
    fleet.reconcile_up(config, "slow")
    members = {m.name: m for m in lifecycle.list_containers(config)}
    shared = members["example-1"].container_id
    assert members["slow-1"].container_id == shared  # independent runtimes, same id
    # What session-host-prepare records for A and the bridge hands back to cleanup.
    runtime = runtime_drivers.runtime_identity("example-1")
    assert runtime.startswith("driver:example-driver/example@")
    assert runtime_drivers.prepared_handle("example-1", shared, runtime) == f"example-1.{shared}"

    # Only A's fleet is removed (A itself stays alive); a fresh process.
    (tmp_path / "containers.yaml").write_text(yaml.safe_dump({"fleets": {
        "slow": {"driver": "other-driver", "size": 1, "relay_enabled": False},
    }}))
    runtime_drivers.clear_bindings()

    def other_calls():
        return json.loads((other_root / "state.json").read_text())["calls"]

    before = len(other_calls())
    expected = ["--expected-runtime", runtime] if retained_runtime else []
    assert cli.main(["session-host-cleanup", "example-1", "--expected-instance", shared,
                     "--expected-user", "vscode", *expected,
                     "--remote-env", "/home/vscode/.agent-containers/launch/env"]) != 0
    err = capsys.readouterr().err
    assert ("no longer owns it" if retained_runtime else "no longer routed by its runtime") in err
    assert cli.main(["session-host-state", "example-1", "--expected-instance", shared,
                     *expected]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "missing"
    # Inventory reads aside, nothing is inspected, executed or copied on B.
    assert [c["verb"] for c in other_calls()[before:] if c["verb"] != "list"] == []


def _register(fake, name, *extra):
    (fake.drivers / f"{name}.json").write_text(json.dumps({
        "schema_version": 1, "name": name, "command": [sys.executable, str(FAKE), *extra],
    }))


def _calls(root):
    path = root / "state.json"
    return json.loads(path.read_text())["calls"] if path.exists() else []


@pytest.mark.parametrize("retained_runtime", [True, False])
def test_session_host_cleanup_refuses_a_fleet_redriven_to_a_same_name_same_id_member(
    fake, no_docker, tmp_path, monkeypatch, capsys, retained_runtime,
):
    config = _write_config(tmp_path, monkeypatch)
    fleet.reconcile_up(config, "example")
    [prepared] = lifecycle.list_containers(config)
    runtime = runtime_drivers.runtime_identity("example-1")  # recorded by prepare

    # The same fleet now uses an independent driver B that reports the same
    # member name with the same instance id.
    other_root = tmp_path / "other-runtime"
    _register(fake, "other-driver", "--root", str(other_root))
    redriven = _write_config(tmp_path, monkeypatch, driver="other-driver")
    fleet.reconcile_up(redriven, "example")
    [replacement] = lifecycle.list_containers(redriven)
    assert (replacement.name, replacement.container_id) == ("example-1", prepared.container_id)
    assert runtime_drivers.runtime_identity("example-1") != runtime
    runtime_drivers.clear_bindings()  # cleanup runs in a fresh process

    before = len(_calls(other_root))
    expected = ["--expected-runtime", runtime] if retained_runtime else []
    assert cli.main(["session-host-cleanup", "example-1", "--expected-instance",
                     prepared.container_id, "--expected-user", "vscode", *expected,
                     "--remote-env", "/home/vscode/.agent-containers/launch/env"]) != 0
    err = capsys.readouterr().err
    assert ("no longer owns it" if retained_runtime else "requires the runtime identity") in err
    assert cli.main(["session-host-state", "example-1", "--expected-instance",
                     prepared.container_id, *expected]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "missing"
    # Inventory reads aside, no cleanup operation reaches B.
    assert [c["verb"] for c in _calls(other_root)[before:] if c["verb"] != "list"] == []


@pytest.mark.parametrize("ssh_capable, refused", [(True, True), (False, False)])
def test_windows_readiness_refuses_driver_ssh_but_accepts_direct_exec(
    fake, monkeypatch, ssh_capable, refused,
):
    from agent_containers import installer_readiness

    monkeypatch.setenv("FAKE_DRIVER_CAPS", json.dumps({"ssh_transport": ssh_capable}))
    monkeypatch.setattr(installer_readiness, "_is_windows", lambda: True)
    failures = installer_readiness.inspect_toolchain(
        driver_config(), command_finder=lambda name: f"C:/tools/{name}.exe",  # OpenSSH present
    )
    assert any("not supported on Windows hosts" in f for f in failures) is refused
    assert not any("OpenSSH" in f for f in failures)
    if not refused:
        assert failures == ()


def test_replaced_driver_registration_is_never_invoked_again(
    fake, no_docker, tmp_path, monkeypatch,
):
    config = _write_config(tmp_path, monkeypatch)
    fleet.reconcile_up(config, "example")
    lifecycle.list_containers(config)
    old_calls = len(fake.state()["calls"])

    replacement_root = tmp_path / "replacement-runtime"
    _register(fake, "example-driver", "--root", str(replacement_root))
    lifecycle.list_containers(config)  # same process, same config, no clear_bindings()
    assert _calls(replacement_root)  # the current registration answered
    route = runtime_drivers.owner_of("example-1")
    assert route is None or "--root" in route.driver.spec.command
    with pytest.raises(RuntimeError, match="docker CLI not found"):
        lifecycle.inspect_container("example-1")  # unreported by the new runtime: not routed
    assert len(fake.state()["calls"]) == old_calls  # the old executable is retired


def test_removed_driver_registration_retires_its_routes(
    fake, no_docker, tmp_path, monkeypatch,
):
    config = _write_config(tmp_path, monkeypatch)
    fleet.reconcile_up(config, "example")
    [member] = lifecycle.list_containers(config)
    old_calls = len(fake.state()["calls"])

    (fake.drivers / "example-driver.json").unlink()
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    lifecycle.list_containers(config)  # the driver fleet fails; nothing else breaks
    for handle in ("example-1", member.container_id, runtime_drivers.route_handle(member)):
        assert runtime_drivers.owner_of(handle) is None
        with pytest.raises(RuntimeError, match="docker CLI not found"):
            lifecycle.inspect_container(handle)  # Docker path only, never the old driver
    assert len(fake.state()["calls"]) == old_calls


def test_delayed_listing_from_a_replaced_registration_never_rebinds(
    fake, no_docker, tmp_path, monkeypatch,
):
    config = _write_config(tmp_path, monkeypatch)
    fleet.reconcile_up(config, "example")
    retired = runtime_drivers.driver_for_fleet(config, "example", config.fleets["example"])

    replacement_root = tmp_path / "replacement-runtime"
    _register(fake, "example-driver", "--root", str(replacement_root))
    fleet.reconcile_up(config, "example")  # B provisions its own example-1
    lifecycle.list_containers(config)
    current = runtime_drivers.owner_of("example-1").driver
    assert current is not retired and "--root" in current.spec.command

    retired.list_members()  # A's slow listing completes after B's
    assert runtime_drivers.owner_of("example-1").driver is current
    old_calls = len(fake.state()["calls"])
    assert runtime_drivers.run_routed([
        "docker", "exec", "example-1", sys.executable, "-c", "pass",
    ]).returncode == 0
    assert len(fake.state()["calls"]) == old_calls  # never the retired executable
    assert [c["verb"] for c in _calls(replacement_root)][-1] == "exec"


def test_changed_driver_options_change_the_prepared_runtime_identity(
    fake, no_docker, tmp_path, monkeypatch, capsys,
):
    config = _write_config(tmp_path, monkeypatch, driver_options={"profile": "first"})
    fleet.reconcile_up(config, "example")
    [prepared] = lifecycle.list_containers(config)
    runtime = runtime_drivers.runtime_identity("example-1")  # recorded by prepare

    # Only the profile changes; the runtime reports the same name and id.
    changed = _write_config(tmp_path, monkeypatch, driver_options={"profile": "second"})
    [member] = lifecycle.list_containers(changed)
    assert (member.name, member.container_id) == ("example-1", prepared.container_id)
    assert runtime_drivers.runtime_identity("example-1") != runtime
    runtime_drivers.clear_bindings()
    calls = len(fake.state()["calls"])
    assert cli.main(["session-host-cleanup", "example-1", "--expected-instance",
                     prepared.container_id, "--expected-user", "vscode",
                     "--expected-runtime", runtime,
                     "--remote-env", "/home/vscode/.agent-containers/launch/env"]) != 0
    assert "no longer owns it" in capsys.readouterr().err
    assert [c["verb"] for c in fake.state()["calls"][calls:] if c["verb"] != "list"] == []


@pytest.mark.parametrize("retire_by", ["registration", "configuration"])
def test_in_flight_listing_after_retirement_restores_no_routes(
    fake, no_docker, tmp_path, monkeypatch, retire_by,
):
    import threading
    import time

    _register(fake, "example-driver", "--delay", "list", "1.5")  # A lists slowly
    config = _write_config(tmp_path, monkeypatch)
    fleet.reconcile_up(config, "example")
    [member] = lifecycle.list_containers(config)
    handles = ["example-1", member.container_id, runtime_drivers.route_handle(member)]
    driver_a = runtime_drivers.owner_of("example-1").driver

    listing = threading.Thread(target=driver_a.list_members)  # A's listing is blocked
    listing.start()
    time.sleep(0.3)
    if retire_by == "registration":
        (fake.drivers / "example-driver.json").unlink()
        monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    else:
        (tmp_path / "containers.yaml").write_text(yaml.safe_dump({"fleets": {}}))
        config = load_config(strict=True)
    _docker_recorder(monkeypatch)
    lifecycle.list_containers(config)  # retires A while its listing is in flight
    listing.join()  # A's listing now completes

    assert all(runtime_drivers.owner_of(h) is None for h in handles)
    calls = len(fake.state()["calls"])
    assert all(runtime_drivers.run_routed(["docker", "exec", h, "true"]) is None for h in handles)
    assert len(fake.state()["calls"]) == calls  # nothing reaches A


def test_session_host_state_reports_an_unavailable_driver_not_a_missing_member(
    fake, no_docker, tmp_path, monkeypatch, capsys,
):
    config = _write_config(tmp_path, monkeypatch)
    fleet.reconcile_up(config, "example")
    [member] = lifecycle.list_containers(config)
    runtime = runtime_drivers.runtime_identity("example-1")
    runtime_drivers.clear_bindings()  # a fresh process
    monkeypatch.setenv("FAKE_DRIVER_FAIL", "list:runtime_unavailable")

    assert cli.main(["session-host-state", "example-1", "--expected-instance",
                     member.container_id, "--expected-runtime", runtime]) != 0
    captured = capsys.readouterr()
    assert "missing" not in captured.out
    assert "unavailable" in captured.err


def test_ssh_precheck_asks_only_the_owning_fleet(fake, no_docker, tmp_path, monkeypatch):
    import time

    monkeypatch.setenv("FAKE_DRIVER_CAPS", json.dumps({"ssh_transport": True}))
    slow_root = tmp_path / "slow-runtime"
    config = _two_driver_config(
        tmp_path, monkeypatch, fake, ["--root", str(slow_root), "--hang", "list"],
    )
    fleet.reconcile_up(config, "example")
    runtime_drivers.clear_bindings()
    monkeypatch.setattr(lifecycle, "list_containers", lambda *a, **k: pytest.fail("full inventory"))
    started = time.monotonic()
    runtime_drivers.require_ssh_transport(config, "example-1", "The `copilot` verb")
    runtime_drivers.require_ssh_transport(config, "not-a-driver-member", "The `copilot` verb")
    assert time.monotonic() - started < 15
    assert not slow_root.exists()  # the unrelated hung driver was never invoked
