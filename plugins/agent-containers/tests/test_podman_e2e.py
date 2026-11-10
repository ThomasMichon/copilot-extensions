"""Explicit, separately authorized native Podman/SSH fixture qualification."""

import json
import os
import shutil
import sys
import uuid

import pytest

from agent_procutil.owned_process import run_caller_owned
from agent_containers import fleet, lifecycle, runtime_drivers, ssh_transport
from agent_containers.config import ContainersConfig, FleetConfig


pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_CONTAINERS_PODMAN_E2E") != "1",
    reason="requires explicitly authorized native Podman fixture",
)


@pytest.fixture
def bundled_podman(tmp_path, monkeypatch):
    directory = tmp_path / "drivers"
    directory.mkdir()
    (directory / "podman.json").write_text(json.dumps({
        "schema_version": 1, "name": "podman",
        "command": [sys.executable, "-m", "agent_containers.podman_driver"],
    }), encoding="utf-8")
    monkeypatch.setenv("AGENT_CONTAINERS_DRIVERS_DIR", str(directory))
    original_which = shutil.which
    monkeypatch.setattr(
        shutil, "which",
        lambda executable: str(tmp_path / "unapproved-driver")
        if executable == "agent-containers-driver-podman" else original_which(executable),
    )


@pytest.mark.skipif(os.name == "nt", reason="Windows provider SSH remains unsupported")
def test_native_podman_authenticated_provider_flow(tmp_path, monkeypatch, bundled_podman):
    """Create one owned member, authenticate over provider SSH, return and remove it."""
    image = os.environ.get("AGENT_CONTAINERS_PODMAN_E2E_IMAGE")
    assert image, "select an explicitly authorized image; there is no default registry/image"
    executable = os.environ.get("AGENT_CONTAINERS_PODMAN_E2E_EXECUTABLE", "podman")
    assert shutil.which(executable), "selected Podman must already be installed"
    ssh = shutil.which("ssh")
    assert ssh and shutil.which("ssh-keygen"), "existing OpenSSH is required"
    options = {
        "image": image, "executable": executable,
        "pull_policy": os.environ.get("AGENT_CONTAINERS_PODMAN_E2E_PULL_POLICY", "never"),
    }
    if connection := os.environ.get("AGENT_CONTAINERS_PODMAN_E2E_CONNECTION"):
        options["connection"] = connection
    name = "podman-qualification-" + uuid.uuid4().hex[:12]
    selected = FleetConfig(
        driver="podman", driver_options=options, size=1, name_prefix=name,
        exec_user=os.environ.get("AGENT_CONTAINERS_PODMAN_E2E_USER", "vscode"),
        workspace_folder="/workspaces/qualification",
    )
    config = ContainersConfig(fleets={name: selected})
    monkeypatch.setattr(ssh_transport, "_SSH_DIR", tmp_path / "ssh")
    # This fixture is authorized for Podman only, never the host's Docker engine.
    def no_docker(*_args, **_kwargs):
        raise RuntimeError("Docker is not part of this fixture")

    monkeypatch.setattr(lifecycle, "_docker", no_docker)
    runtime_drivers.clear_bindings()
    driver = runtime_drivers.driver_for_fleet(config, name, selected)
    owned_id = None
    try:
        [created] = fleet.reconcile_up(config, name).created
        member = driver.inspect(created)
        owned_id = member["Id"]
        [inventory] = driver.list_members()
        assert inventory.container_id == owned_id and inventory.state == "running"
        handle = f"{created}.{owned_id}"
        plan = runtime_drivers.launch_plan(selected, handle, True, require_ssh=True)
        assert plan.ssh and plan.agent_relay and not plan.driver.capabilities.owns_relay
        transport = ssh_transport.prepare_ssh_config(handle, selected.exec_user)
        result = run_caller_owned(
            [ssh, "-F", transport.config_file, transport.host_alias,
             "printf '%s' authenticated-podman-fixture"],
            timeout=30, max_stdout_bytes=65536, max_stderr_bytes=65536,
        )
        assert result.returncode == 0, "native SSH authentication or dispatch failed"
        assert result.stdout == "authenticated-podman-fixture"
        driver.stop(created, instance_id=owned_id)
        assert driver.state(created, instance_id=owned_id) == "exited"
        driver.start(created, instance_id=owned_id)
        assert driver.state(created, instance_id=owned_id) == "running"
    finally:
        try:
            if owned_id is not None:
                # Only a successfully observed incarnation is a cleanup target.
                driver.remove(created, instance_id=owned_id, force=True)
        finally:
            runtime_drivers.clear_bindings()


def test_native_podman_provider_lifecycle(tmp_path, monkeypatch, bundled_podman):
    """Qualify native lifecycle without claiming authenticated dispatch support."""
    image = os.environ.get("AGENT_CONTAINERS_PODMAN_E2E_IMAGE")
    assert image, "select an explicitly authorized image"
    executable = os.environ.get("AGENT_CONTAINERS_PODMAN_E2E_EXECUTABLE", "podman")
    assert shutil.which(executable), "selected Podman must already be installed"
    options = {"image": image, "executable": executable, "pull_policy": "never"}
    if connection := os.environ.get("AGENT_CONTAINERS_PODMAN_E2E_CONNECTION"):
        options["connection"] = connection
    name = "podman-native-" + uuid.uuid4().hex[:12]
    selected = FleetConfig(
        driver="podman", driver_options=options, size=1, name_prefix=name,
        exec_user=os.environ.get("AGENT_CONTAINERS_PODMAN_E2E_USER", "vscode"),
        workspace_folder="/workspaces/qualification",
    )
    config = ContainersConfig(fleets={name: selected})

    def no_docker(*_args, **_kwargs):
        raise RuntimeError("Docker is not part of this fixture")

    monkeypatch.setattr(lifecycle, "_docker", no_docker)
    runtime_drivers.clear_bindings()
    driver = runtime_drivers.driver_for_fleet(config, name, selected)
    owned_id = None
    print(f"Native fixture fleet: {name}")
    try:
        [created] = fleet.reconcile_up(config, name).created
        owned_id = driver.inspect(created)["Id"]
        [member] = driver.list_members()
        assert member.container_id == owned_id and member.state == "running"
        print(f"Observed native fixture: {created}.{owned_id}")
        plan = runtime_drivers.launch_plan(selected, f"{created}.{owned_id}", True, require_ssh=True)
        assert plan.ssh and plan.agent_relay and not plan.driver.capabilities.owns_relay
        result = driver.exec(
            created, ["/bin/printf", "%s", "literal; native $value"],
            user=selected.exec_user, instance_id=owned_id,
        )
        assert result.exit_code == 0 and result.stdout == "literal; native $value"
        host_keys = driver.exec(
            created, ["/bin/sh", "-c", "find /etc/ssh -maxdepth 1 -name 'ssh_host_*' | wc -l"],
            user="root", instance_id=owned_id,
        )
        assert host_keys.exit_code == 0 and host_keys.stdout.strip() == "0"
        source = tmp_path / "native-fixture.txt"
        source.write_text("native-copy-fixture", encoding="utf-8")
        driver.copy_in(created, source.resolve(), "/tmp/native-fixture.txt", instance_id=owned_id)
        copied = driver.exec(
            created, ["/bin/cat", "/tmp/native-fixture.txt"], instance_id=owned_id,
        )
        assert copied.exit_code == 0 and copied.stdout == "native-copy-fixture"
        command = [executable]
        if connection := options.get("connection"):
            command += ["--connection", connection]
        paused = run_caller_owned(
            [*command, "pause", owned_id], timeout=30,
            max_stdout_bytes=65536, max_stderr_bytes=65536,
        )
        assert paused.returncode == 0, "native fixture pause failed"
        assert driver.state(created, instance_id=owned_id) == "paused"
        driver.start(created, instance_id=owned_id)
        assert driver.state(created, instance_id=owned_id) == "running"
        driver.stop(created, instance_id=owned_id)
        assert driver.state(created, instance_id=owned_id) == "exited"
        driver.start(created, instance_id=owned_id)
        assert driver.state(created, instance_id=owned_id) == "running"
    finally:
        try:
            if owned_id is not None:
                driver.remove(created, instance_id=owned_id, force=True)
                assert driver.state(created, instance_id=owned_id) is None
                print(f"Removed exact native fixture: {created}.{owned_id}")
        finally:
            runtime_drivers.clear_bindings()
