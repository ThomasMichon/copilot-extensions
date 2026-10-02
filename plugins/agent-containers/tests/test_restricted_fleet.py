"""Restricted-fleet transport boundary tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_containers import fleet as fleet_mod
from agent_containers.config import (
    RESTRICTED_POLICY_VERSION,
    ContainersConfig,
    FleetConfig,
)
from agent_containers.lifecycle import DockerContainerInfo, restricted_policy_errors
from agent_containers.rescue import RescueError


def _ok(stdout: str = ""):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def test_recreate_member_replaces_one_identity_checked_trusted_container(
    monkeypatch,
):
    old = DockerContainerInfo(
        name="example-1",
        container_id="a" * 64,
        image="example",
        state="running",
        status="Up",
        fleet="example",
    )
    new = DockerContainerInfo(
        name="example-1",
        container_id="b" * 64,
        image="example",
        state="running",
        status="Up",
        fleet="example",
    )
    config = ContainersConfig(
        fleets={
            "example": FleetConfig(
                devcontainer_path="D:/src/example",
            ),
        },
    )
    calls = []
    monkeypatch.setattr(fleet_mod, "_check_docker", lambda: None)
    monkeypatch.setattr(
        fleet_mod,
        "get_container",
        lambda *_args: old if not calls else new,
    )
    monkeypatch.setattr(
        fleet_mod,
        "remove_container",
        lambda name, force=False, timeout=120: calls.append(
            ("remove", name, force, timeout)
        ),
    )
    monkeypatch.setattr(
        fleet_mod,
        "_devcontainer_up",
        lambda *args, **kwargs: calls.append(("create", args[2])) or args[2],
    )

    result = fleet_mod.recreate_member(
        config,
        "example-1",
        expected_container_id=old.container_id,
    )

    assert result["old_container_id"] == old.container_id
    assert result["new_container_id"] == new.container_id
    assert result["identity_changed"] is True
    assert calls == [
        ("remove", old.container_id, True, 600.0),
        ("create", "example-1"),
    ]


def test_recreate_member_rejects_mismatched_replacement_posture(monkeypatch):
    old = DockerContainerInfo(
        name="example-1",
        container_id="a" * 64,
        image="example",
        state="running",
        status="Up",
        fleet="example",
    )
    mismatched = DockerContainerInfo(
        name="example-1",
        container_id="b" * 64,
        image="example",
        state="running",
        status="Up",
        fleet="other",
        security_profile="restricted",
    )
    config = ContainersConfig(
        fleets={
            "example": FleetConfig(
                devcontainer_path="D:/src/example",
            ),
        },
    )
    calls = []
    monkeypatch.setattr(fleet_mod, "_check_docker", lambda: None)
    monkeypatch.setattr(
        fleet_mod,
        "get_container",
        lambda *_args: old if not calls else mismatched,
    )
    monkeypatch.setattr(
        fleet_mod,
        "remove_container",
        lambda *args, **kwargs: calls.append(args),
    )
    monkeypatch.setattr(
        fleet_mod,
        "_devcontainer_up",
        lambda *args, **kwargs: args[2],
    )

    with pytest.raises(RuntimeError, match="does not match"):
        fleet_mod.recreate_member(
            config,
            "example-1",
            expected_container_id=old.container_id,
        )


def test_restricted_image_run_applies_boundary_flags(monkeypatch):
    calls: list[list[str]] = []

    def fake_docker(args, timeout=30):
        calls.append(args)
        return _ok("container-id\n")

    monkeypatch.setattr(fleet_mod, "_docker", fake_docker)
    monkeypatch.setattr(fleet_mod, "_validate_restricted_network", lambda network: None)
    monkeypatch.setattr(
        fleet_mod,
        "_image_user",
        lambda image, user, **kwargs: (1000, 1000, "/home/vscode"),
    )
    monkeypatch.setattr(fleet_mod, "_image_id", lambda image: "sha256:image")
    fleet = FleetConfig(
        image="example/agent:latest",
        security_profile="restricted",
        network="model-only",
        memory="6g",
        cpus=3,
        pids_limit=128,
        environment={"MODEL_NAME": "local-model"},
    )

    name = fleet_mod._image_run(
        "sandbox",
        fleet,
        "sandbox-1",
        workspace_folder="/workspace",
        exec_user="vscode",
    )

    assert name == "sandbox-1"
    run = calls[0]
    assert run[:3] == ["run", "-d", "--name"]
    assert "--read-only" in run
    assert "agent-containers.security-profile=restricted" in run
    policy_labels = [
        x for x in run if x.startswith("agent-containers.security-policy=")
    ]
    assert len(policy_labels) == 1
    assert "agent-containers.security-image-id=sha256:image" in run
    assert "agent-containers.security-uid=1000" in run
    assert "agent-containers.security-gid=1000" in run
    assert ["--cap-drop=ALL"] == [x for x in run if x == "--cap-drop=ALL"]
    assert "--security-opt=no-new-privileges" in run
    assert run[run.index("--network") + 1] == "model-only"
    assert run[run.index("--memory") + 1] == "6g"
    assert run[run.index("--cpus") + 1] == "3"
    assert run[run.index("--pids-limit") + 1] == "128"
    assert "--mount" not in run
    assert "--volume" not in run
    assert "-v" not in run
    tmpfs = [run[i + 1] for i, value in enumerate(run) if value == "--tmpfs"]
    assert {value.split(":", 1)[0] for value in tmpfs} == {
        "/workspace",
        "/home/vscode",
        "/tmp",  # noqa: S108
        "/run",
    }
    assert "HOME=/home/vscode" in run
    assert "MODEL_NAME=local-model" in run
    assert "--add-host=host.docker.internal:host-gateway" not in run
    assert RESTRICTED_POLICY_VERSION == 2


def test_trusted_image_run_wires_host_mounts_and_systemd_capability(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        fleet_mod,
        "_docker",
        lambda args, timeout=30: calls.append(args) or _ok("container-id\n"),
    )
    monkeypatch.setattr(
        fleet_mod, "_trusted_mount_owner", lambda image, user: (1000, 1000)
    )
    ensure_owned_calls: list[tuple[str, int, int]] = []
    monkeypatch.setattr(
        fleet_mod,
        "_ensure_owned_dir",
        lambda path, uid, gid: ensure_owned_calls.append((path, uid, gid)),
    )
    fleet = FleetConfig(
        image="example/agent:latest",
        security_profile="trusted",
        host_workspace_path="/mnt/data/workspaces",
        host_home_path="/mnt/data/home",
        home_folder="/home/node",
        systemd_capable=True,
    )

    name = fleet_mod._image_run(
        "example",
        fleet,
        "example-1",
        workspace_folder="/workspace/example",
        exec_user="node",
    )

    assert name == "example-1"
    run = calls[0]
    assert "-v" in run
    mounts = [run[i + 1] for i, value in enumerate(run) if value == "-v"]
    member_workspace = fleet_mod._member_host_path(
        "/mnt/data/workspaces", "example-1", label="host_workspace_path"
    )
    member_home = fleet_mod._member_host_path(
        "/mnt/data/home", "example-1", label="host_home_path"
    )
    # Each fleet member mounts its OWN subdirectory (keyed by its container
    # name) under the configured parent path -- never the bare parent path
    # directly, which would collide across a size > 1 fleet's members.
    assert f"{member_workspace}:/workspace/example" in mounts
    assert f"{member_home}:/home/node" in mounts
    # Each member directory was created/chowned to the resolved exec_user
    # uid/gid BEFORE the mount arg was added -- a missing bind-mount source
    # would otherwise be auto-created by Docker as root.
    assert (member_workspace, 1000, 1000) in ensure_owned_calls
    assert (member_home, 1000, 1000) in ensure_owned_calls
    assert run[run.index("--cap-add") + 1] == "SYS_ADMIN"
    # PID 1 (systemd) must boot as root regardless of the image's own
    # default USER -- exec_user only governs later `docker exec` calls.
    assert run[run.index("--user") + 1] == "root"
    tmpfs = [run[i + 1] for i, value in enumerate(run) if value == "--tmpfs"]
    assert "/run:rw,exec" in tmpfs
    assert "/run/lock:rw,exec" in tmpfs
    assert "-t" in run
    assert "container=docker" in run
    # The launched command remounts /sys/fs/cgroup rw before exec'ing
    # systemd -- NOT the historical `sleep infinity` placeholder.
    assert run[-4:] == [
        "example/agent:latest",
        "bash", "-c",
        "mount -o remount,rw /sys/fs/cgroup && exec /lib/systemd/systemd",
    ]
    assert "--cap-drop=ALL" not in run
    assert "--read-only" not in run


def test_trusted_image_run_without_systemd_capable_keeps_sleep_infinity(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        fleet_mod,
        "_docker",
        lambda args, timeout=30: calls.append(args) or _ok("container-id\n"),
    )
    fleet = FleetConfig(image="example/agent:latest", security_profile="trusted")

    fleet_mod._image_run(
        "example",
        fleet,
        "example-1",
        workspace_folder="/workspace/example",
        exec_user="node",
    )

    run = calls[0]
    assert run[-3:] == ["example/agent:latest", "sleep", "infinity"]
    assert "-v" not in run
    assert "--cap-add" not in run


def test_trusted_image_run_namespaces_host_paths_per_fleet_member(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        fleet_mod,
        "_docker",
        lambda args, timeout=30: calls.append(args) or _ok("container-id\n"),
    )
    monkeypatch.setattr(
        fleet_mod, "_trusted_mount_owner", lambda image, user: (1000, 1000)
    )
    monkeypatch.setattr(fleet_mod, "_ensure_owned_dir", lambda path, uid, gid: None)
    fleet = FleetConfig(
        image="example/agent:latest",
        security_profile="trusted",
        host_workspace_path="/mnt/data/workspaces",
    )

    fleet_mod._image_run(
        "example", fleet, "example-1",
        workspace_folder="/workspace/example", exec_user="node",
    )
    fleet_mod._image_run(
        "example", fleet, "example-2",
        workspace_folder="/workspace/example", exec_user="node",
    )

    mounts_1 = [calls[0][i + 1] for i, v in enumerate(calls[0]) if v == "-v"]
    mounts_2 = [calls[1][i + 1] for i, v in enumerate(calls[1]) if v == "-v"]
    member_1 = fleet_mod._member_host_path(
        "/mnt/data/workspaces", "example-1", label="host_workspace_path"
    )
    member_2 = fleet_mod._member_host_path(
        "/mnt/data/workspaces", "example-2", label="host_workspace_path"
    )
    assert mounts_1 == [f"{member_1}:/workspace/example"]
    assert mounts_2 == [f"{member_2}:/workspace/example"]


def test_trusted_image_run_rejects_path_traversal_via_name_prefix(monkeypatch):
    monkeypatch.setattr(fleet_mod, "_docker", lambda args, timeout=30: _ok("id\n"))
    monkeypatch.setattr(
        fleet_mod, "_trusted_mount_owner", lambda image, user: (1000, 1000)
    )
    ensure_owned_calls: list[str] = []
    monkeypatch.setattr(
        fleet_mod, "_ensure_owned_dir",
        lambda path, uid, gid: ensure_owned_calls.append(path),
    )
    fleet = FleetConfig(
        image="example/agent:latest",
        security_profile="trusted",
        host_workspace_path="/mnt/data/workspaces",
    )

    with pytest.raises(RuntimeError, match="escapes the configured"):
        fleet_mod._image_run(
            "example",
            fleet,
            # a malicious/misconfigured name_prefix could produce this
            "../../etc/example-1",
            workspace_folder="/workspace/example",
            exec_user="node",
        )
    # The rejection must happen BEFORE any directory is created/chowned.
    assert ensure_owned_calls == []


def test_ensure_owned_dir_creates_and_chowns(monkeypatch, tmp_path):
    target = tmp_path / "workspaces" / "example-1"
    chown_calls: list[tuple[str, int, int]] = []
    monkeypatch.setattr(
        fleet_mod.os,
        "chown",
        lambda path, uid, gid: chown_calls.append((path, uid, gid)),
        raising=False,
    )

    fleet_mod._ensure_owned_dir(str(target), 1000, 1000)

    assert target.is_dir()
    if fleet_mod.os.name != "nt":
        assert chown_calls == [(str(target), 1000, 1000)]


def test_member_host_path_accepts_a_normal_member_name(tmp_path):
    parent = tmp_path / "workspaces"
    resolved = fleet_mod._member_host_path(str(parent), "example-1", label="host_workspace_path")
    assert resolved == str((parent / "example-1").resolve())


@pytest.mark.parametrize("malicious_name", ["../escape", "../../etc/passwd", "/etc/passwd"])
def test_member_host_path_rejects_traversal(tmp_path, malicious_name):
    parent = tmp_path / "workspaces"
    with pytest.raises(RuntimeError, match="escapes the configured"):
        fleet_mod._member_host_path(str(parent), malicious_name, label="host_workspace_path")


def test_restricted_network_defaults_to_none(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        fleet_mod,
        "_docker",
        lambda args, timeout=30: calls.append(args) or _ok(),
    )
    monkeypatch.setattr(
        fleet_mod,
        "_image_user",
        lambda image, user, **kwargs: (1000, 1000, "/home/vscode"),
    )
    monkeypatch.setattr(fleet_mod, "_image_id", lambda image: "sha256:image")
    fleet = FleetConfig(
        image="example/agent:latest",
        security_profile="restricted",
    )

    fleet_mod._image_run(
        "sandbox",
        fleet,
        "sandbox-1",
        workspace_folder="/workspace",
        exec_user="vscode",
    )

    run = calls[0]
    assert run[run.index("--network") + 1] == "none"


def test_restricted_devcontainer_backend_is_refused(monkeypatch):
    config = ContainersConfig()
    config.fleets["sandbox"] = FleetConfig(
        devcontainer_path="/tmp/spec",  # noqa: S108
        security_profile="restricted",
    )
    monkeypatch.setattr(fleet_mod, "_check_docker", lambda: None)

    with pytest.raises(RuntimeError, match="must use the image backend"):
        fleet_mod.up(config, "sandbox")


def test_restricted_existing_container_with_stale_policy_is_refused(monkeypatch):
    config = ContainersConfig()
    config.fleets["sandbox"] = FleetConfig(
        image="example/agent",
        security_profile="restricted",
        acp_command="minimal-agent --stdio",
    )
    stale = SimpleNamespace(
        name="sandbox-1",
        security_profile="restricted",
        security_policy="old-policy",
        security_image_id="sha256:image",
    )
    monkeypatch.setattr(fleet_mod, "_check_docker", lambda: None)
    monkeypatch.setattr(fleet_mod, "_fleet_members", lambda cfg, name: [stale])
    monkeypatch.setattr(fleet_mod, "_image_id", lambda image: "sha256:image")

    with pytest.raises(RuntimeError, match="stale or mismatched security policy"):
        fleet_mod.up(config, "sandbox")


def test_restricted_policy_inspects_effective_docker_boundary(monkeypatch):
    fleet = FleetConfig(
        image="example/agent:latest",
        security_profile="restricted",
        acp_command="minimal-agent --stdio",
    )
    policy = fleet.security_policy_fingerprint("/workspace", "agent")
    info = DockerContainerInfo(
        name="sandbox-1",
        container_id="cid",
        image="example/agent:latest",
        state="running",
        status="Up",
        fleet="sandbox",
        security_profile="restricted",
        security_policy=policy,
    )
    doc = {
        "Config": {
            "Image": "example/agent:latest",
            "Env": ["HOME=/home/agent"],
            "Labels": {
                "agent-containers.security-profile": "restricted",
                "agent-containers.security-policy": policy,
                "agent-containers.security-home": "/home/agent",
                "agent-containers.security-uid": "1000",
                "agent-containers.security-gid": "1000",
                "agent-containers.security-image-id": "sha256:image",
            },
        },
        "Image": "sha256:image",
        "HostConfig": {
            "ReadonlyRootfs": True,
            "Privileged": False,
            "CapDrop": ["ALL"],
            "CapAdd": None,
            "SecurityOpt": ["no-new-privileges:true"],
            "Binds": None,
            "Devices": [],
            "DeviceRequests": None,
            "PidMode": "",
            "IpcMode": "private",
            "UTSMode": "",
            "UsernsMode": "",
            "PortBindings": {},
            "PublishAllPorts": False,
            "ExtraHosts": None,
            "NetworkMode": "none",
            "Memory": 4 * 1024**3,
            "MemorySwap": 4 * 1024**3,
            "NanoCpus": 2_000_000_000,
            "PidsLimit": 256,
            "Tmpfs": {
                "/workspace": "rw,nosuid,nodev,exec,size=2g,uid=1000,gid=1000,mode=0700",
                "/home/agent": "rw,nosuid,nodev,exec,size=512m,uid=1000,gid=1000,mode=0700",
                "/tmp": "rw,nosuid,nodev,size=512m",  # noqa: S108
                "/run": "rw,nosuid,nodev,size=64m",
            },
        },
        "Mounts": [],
        "NetworkSettings": {"Networks": {"none": {}}},
    }
    monkeypatch.setattr(
        "agent_containers.lifecycle.inspect_container",
        lambda name: doc,
    )
    monkeypatch.setattr(
        "agent_containers.lifecycle._docker",
        lambda args, timeout=30: _ok("sha256:image\n"),
    )

    assert restricted_policy_errors(
        info,
        fleet,
        workspace_folder="/workspace",
        exec_user="agent",
    ) == []

    doc["HostConfig"]["ReadonlyRootfs"] = False
    errors = restricted_policy_errors(
        info,
        fleet,
        workspace_folder="/workspace",
        exec_user="agent",
    )
    assert "root filesystem is not read-only" in errors

    doc["HostConfig"]["ReadonlyRootfs"] = True
    doc["HostConfig"]["CapAdd"] = ["SYS_ADMIN"]
    doc["HostConfig"]["SecurityOpt"].append("seccomp=unconfined")
    errors = restricted_policy_errors(
        info,
        fleet,
        workspace_folder="/workspace",
        exec_user="agent",
    )
    assert "Linux capabilities are re-added" in errors
    assert "an unconfined security profile is present" in errors


def test_restricted_policy_migrating_skips_only_current_config_checks(monkeypatch):
    """copilot-extensions#4933 follow-up: ``migrating=True`` exempts the
    CURRENT-config comparisons (image/policy/environment/network/memory/
    cpu/pids/tmpfs sizing) a deliberate profile migration necessarily no
    longer matches, while every FIXED security invariant about the
    container's own build still applies."""
    old_fleet = FleetConfig(
        image="example/agent:latest",
        security_profile="restricted",
        acp_command="minimal-agent --stdio",
    )
    policy = old_fleet.security_policy_fingerprint("/workspace", "agent")
    info = DockerContainerInfo(
        name="sandbox-1",
        container_id="cid",
        image="example/agent:latest",
        state="running",
        status="Up",
        fleet="sandbox",
        security_profile="restricted",
        security_policy=policy,
    )
    doc = {
        "Config": {
            "Image": "example/agent:latest",
            "Env": ["HOME=/home/agent"],
            "Labels": {
                "agent-containers.security-profile": "restricted",
                "agent-containers.security-policy": policy,
                "agent-containers.security-home": "/home/agent",
                "agent-containers.security-uid": "1000",
                "agent-containers.security-gid": "1000",
                "agent-containers.security-image-id": "sha256:image",
            },
        },
        "Image": "sha256:image",
        "HostConfig": {
            "ReadonlyRootfs": True,
            "Privileged": False,
            "CapDrop": ["ALL"],
            "CapAdd": None,
            "SecurityOpt": ["no-new-privileges"],
            "Binds": None,
            "Devices": [],
            "DeviceRequests": None,
            "PidMode": "",
            "IpcMode": "private",
            "UTSMode": "",
            "UsernsMode": "",
            "PortBindings": {},
            "PublishAllPorts": False,
            "ExtraHosts": None,
            "NetworkMode": "none",
            "Memory": 4 * 1024**3,
            "MemorySwap": 4 * 1024**3,
            "NanoCpus": 2_000_000_000,
            "PidsLimit": 256,
            "Tmpfs": {
                "/workspace": "rw,nosuid,nodev,exec,size=2g,uid=1000,gid=1000,mode=0700",
                "/home/agent": "rw,nosuid,nodev,exec,size=512m,uid=1000,gid=1000,mode=0700",
                "/tmp": "rw,nosuid,nodev,size=512m",  # noqa: S108
                "/run": "rw,nosuid,nodev,size=64m",
            },
        },
        "Mounts": [],
        "NetworkSettings": {"Networks": {"none": {}}},
    }
    monkeypatch.setattr(
        "agent_containers.lifecycle.inspect_container",
        lambda name: doc,
    )
    monkeypatch.setattr(
        "agent_containers.lifecycle._docker",
        lambda args, timeout=30: _ok("sha256:image\n"),
    )

    # The CURRENT (now-trusted) fleet config: image, environment, and
    # network/memory/cpu/pids all necessarily differ from the restricted-
    # built container's own real values -- none of that should block a
    # migration.
    new_fleet = FleetConfig(
        image="example/agent:v2",
        security_profile="trusted",
        environment={"SOME_VAR": "changed"},
    )

    assert restricted_policy_errors(
        info,
        new_fleet,
        workspace_folder="/workspace",
        exec_user="agent",
        migrating=True,
    ) == []

    # A FIXED invariant violation still blocks, even with migrating=True.
    doc["HostConfig"]["Privileged"] = True
    errors = restricted_policy_errors(
        info,
        new_fleet,
        workspace_folder="/workspace",
        exec_user="agent",
        migrating=True,
    )
    assert "container is privileged" in errors


def test_start_restricted_validates_before_start(monkeypatch):
    config = ContainersConfig()
    config.fleets["sandbox"] = FleetConfig(
        image="example/agent",
        security_profile="restricted",
        acp_command="minimal-agent --stdio",
    )
    stopped = DockerContainerInfo(
        name="sandbox-1",
        container_id="cid",
        image="example/agent",
        state="exited",
        status="Exited",
        fleet="sandbox",
        security_profile="restricted",
    )
    monkeypatch.setattr(
        fleet_mod,
        "_fleet_members",
        lambda cfg, fleet_name: [stopped],
    )
    monkeypatch.setattr(
        "agent_containers.lifecycle.restricted_policy_errors",
        lambda *a, **k: ["root filesystem is not read-only"],
    )
    monkeypatch.setattr(
        fleet_mod,
        "start_container",
        lambda name: (_ for _ in ()).throw(
            AssertionError("unsafe container must not start")
        ),
    )

    with pytest.raises(RuntimeError, match="does not satisfy"):
        fleet_mod.start(config, "sandbox")


def test_restricted_stale_container_recreated_with_recreate_flag(monkeypatch):
    """Confirmed-idle drifted members are rescued and recreated fresh."""
    from agent_containers import replacement

    config = ContainersConfig()
    config.fleets["sandbox"] = FleetConfig(
        image="example/agent",
        security_profile="restricted",
        acp_command="minimal-agent --stdio",
    )
    stale = DockerContainerInfo(
        name="sandbox-1",
        container_id="old-instance",
        image="example/agent",
        state="running",
        status="Up",
        fleet="sandbox",
        security_profile="restricted",
        security_policy="old-policy",
        security_image_id="sha256:old",
    )
    members = {"list": [stale]}
    monkeypatch.setattr(fleet_mod, "_check_docker", lambda: None)
    monkeypatch.setattr(
        fleet_mod, "_fleet_members", lambda cfg, name: list(members["list"])
    )
    # Current image differs from the running member -> image drift.
    monkeypatch.setattr(fleet_mod, "_image_id", lambda image: "sha256:new")

    monkeypatch.setattr(
        replacement,
        "destroy_restricted_member",
        lambda *_args, **_kwargs: replacement.DestructiveResult(
            "sandbox-1",
            "removed",
            rescue={"status": "verified"},
        ),
    )

    provisioned: list[str] = []

    def fake_image_run(fleet_name, fleet, name, **kwargs):
        provisioned.append(name)
        return name

    monkeypatch.setattr(fleet_mod, "_image_run", fake_image_run)

    created = fleet_mod.up(config, "sandbox", recreate=True)

    assert provisioned == ["sandbox-1"]  # re-provisioned under the same name
    assert created == ["sandbox-1"]


def test_restricted_recreate_defers_members_independently(monkeypatch):
    from agent_containers import replacement

    config = ContainersConfig()
    config.fleets["sandbox"] = FleetConfig(
        image="example/agent",
        security_profile="restricted",
        acp_command="minimal-agent --stdio",
        size=2,
    )
    members = [
        DockerContainerInfo(
            name=f"sandbox-{index}",
            container_id=f"old-{index}",
            image="example/agent",
            state="running",
            status="Up",
            fleet="sandbox",
            security_profile="restricted",
            security_policy="old-policy",
            security_image_id="sha256:old",
        )
        for index in (1, 2)
    ]
    monkeypatch.setattr(fleet_mod, "_check_docker", lambda: None)
    monkeypatch.setattr(fleet_mod, "_fleet_members", lambda *_args: members)
    monkeypatch.setattr(
        fleet_mod,
        "inspect_container",
        lambda name: {
            "Id": name,
            "State": {"StartedAt": "2026-01-01T00:00:00Z"},
        },
    )
    monkeypatch.setattr(fleet_mod, "_image_id", lambda _image: "sha256:new")

    def destroy(_config, _fleet, info, **_kwargs):
        if info.name == "sandbox-1":
            return replacement.DestructiveResult(
                info.name,
                "removed",
                rescue={"status": "verified"},
            )
        return replacement.DestructiveResult(
            info.name,
            "deferred",
            "active Copilot session-state lock present",
        )

    monkeypatch.setattr(replacement, "destroy_restricted_member", destroy)
    provisioned = []
    monkeypatch.setattr(
        fleet_mod,
        "_image_run",
        lambda _fleet_name, _fleet, name, **_kwargs: (
            provisioned.append(name) or name
        ),
    )

    result = fleet_mod.reconcile_up(config, "sandbox", recreate=True)

    assert result.created == ["sandbox-1"]
    assert result.recreated == ["sandbox-1"]
    assert result.deferred == {
        "sandbox-2": "active Copilot session-state lock present"
    }
    assert provisioned == ["sandbox-1"]


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("docker rm timed out after 30s"),
        RescueError("rescue generation unavailable"),
    ],
)
def test_recreate_timeout_defers_one_member_and_continues(
    monkeypatch,
    failure,
):
    from agent_containers import replacement

    config = ContainersConfig(
        fleets={
            "sandbox": FleetConfig(
                image="example/agent",
                security_profile="restricted",
                acp_command="minimal-agent --stdio",
                size=2,
            )
        }
    )
    members = [
        DockerContainerInfo(
            name=f"sandbox-{index}",
            container_id=f"old-{index}",
            image="example/agent",
            state="running",
            status="Up",
            fleet="sandbox",
            security_profile="restricted",
            security_policy="old",
            security_image_id="sha256:old",
        )
        for index in (1, 2)
    ]
    monkeypatch.setattr(fleet_mod, "_check_docker", lambda: None)
    monkeypatch.setattr(fleet_mod, "_fleet_members", lambda *_args: members)
    monkeypatch.setattr(fleet_mod, "_image_id", lambda _image: "sha256:new")

    def destroy(_config, _fleet, info, **_kwargs):
        if info.name == "sandbox-1":
            raise failure
        return replacement.DestructiveResult(info.name, "removed")

    monkeypatch.setattr(replacement, "destroy_restricted_member", destroy)
    created = []
    monkeypatch.setattr(
        fleet_mod,
        "_image_run",
        lambda _fleet_name, _fleet, name, **_kwargs: created.append(name) or name,
    )

    result = fleet_mod.reconcile_up(config, "sandbox", recreate=True)

    assert str(failure) in result.deferred["sandbox-1"]
    assert result.recreated == ["sandbox-2"]
    assert created == ["sandbox-2"]


def test_trusted_remove_behavior_does_not_enter_restricted_rescue(monkeypatch):
    from agent_containers import replacement

    config = ContainersConfig(
        fleets={"example": FleetConfig(image="example/agent")}
    )
    member = DockerContainerInfo(
        name="example-1",
        container_id="instance",
        image="example/agent",
        state="running",
        status="Up",
        fleet="example",
        security_profile="trusted",
    )
    monkeypatch.setattr(fleet_mod, "_fleet_members", lambda *_args: [member])
    monkeypatch.setattr(
        replacement,
        "destroy_restricted_member",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("trusted member must not enter restricted rescue")
        ),
    )
    removed = []
    monkeypatch.setattr(
        fleet_mod,
        "remove_container",
        lambda name, force=False: removed.append((name, force)),
    )

    result = fleet_mod.remove_fleet(config, "example", force=True)

    assert result.removed == ["example-1"]
    assert result.deferred == {}
    assert removed == [("example-1", True)]


def test_requested_restricted_fleet_rejects_foreign_trusted_label(monkeypatch):
    config = ContainersConfig(
        fleets={
            "sandbox": FleetConfig(
                image="example/restricted",
                security_profile="restricted",
                acp_command="minimal-agent --stdio",
            ),
            "trusted": FleetConfig(image="example/trusted"),
        }
    )
    conflict = DockerContainerInfo(
        name="sandbox-1",
        container_id="instance",
        image="example/trusted",
        state="running",
        status="Up",
        fleet="trusted",
        security_profile="trusted",
    )
    monkeypatch.setattr(fleet_mod, "_fleet_members", lambda *_args: [conflict])
    monkeypatch.setattr(
        fleet_mod,
        "remove_container",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("foreign label must not bypass restricted lifecycle")
        ),
    )

    result = fleet_mod.remove_fleet(config, "sandbox", force=True)

    assert result.removed == []
    assert "conflicts with requested fleet" in result.deferred["sandbox-1"]


def test_rescue_capture_fleet_captures_running_members_without_stopping(monkeypatch):
    from agent_containers import replacement

    config = ContainersConfig(
        fleets={
            "sandbox": FleetConfig(
                image="example/agent",
                security_profile="restricted",
                acp_command="minimal-agent --stdio",
            )
        }
    )
    members = [
        DockerContainerInfo(
            name=f"sandbox-{index}",
            container_id=f"instance-{index}",
            image="example/agent",
            state="running",
            status="Up",
            fleet="sandbox",
            security_profile="restricted",
        )
        for index in (1, 2)
    ]
    monkeypatch.setattr(fleet_mod, "_fleet_members", lambda *_args: members)
    monkeypatch.setattr(
        fleet_mod,
        "stop_container",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("rescue-capture must not stop the container")
        ),
    )
    monkeypatch.setattr(
        fleet_mod,
        "remove_container",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("rescue-capture must not remove the container")
        ),
    )

    def safe_capture(_config, _fleet, info, **_kwargs):
        if info.name == "sandbox-1":
            return replacement.DestructiveResult(
                info.name,
                "captured",
                rescue={"status": "verified"},
            )
        return replacement.DestructiveResult(
            info.name,
            "deferred",
            "active Copilot session-state lock present",
        )

    monkeypatch.setattr(replacement, "rescue_capture_restricted_member", safe_capture)

    result = fleet_mod.rescue_capture_fleet(config, "sandbox")

    assert result.captured == ["sandbox-1"]
    assert result.rescues == {"sandbox-1": {"status": "verified"}}
    assert result.deferred == {
        "sandbox-2": "active Copilot session-state lock present"
    }


def test_rescue_capture_fleet_defers_nonrunning_members(monkeypatch):
    from agent_containers import replacement

    config = ContainersConfig(
        fleets={
            "sandbox": FleetConfig(
                image="example/agent",
                security_profile="restricted",
                acp_command="minimal-agent --stdio",
            )
        }
    )
    members = [
        DockerContainerInfo(
            name="sandbox-1",
            container_id="instance-1",
            image="example/agent",
            state="exited",
            status="Exited",
            fleet="sandbox",
            security_profile="restricted",
        )
    ]
    monkeypatch.setattr(fleet_mod, "_fleet_members", lambda *_args: members)
    monkeypatch.setattr(
        replacement,
        "rescue_capture_restricted_member",
        lambda *_args, **_kwargs: replacement.DestructiveResult(
            "sandbox-1",
            "deferred",
            "container is not running; nothing to capture",
        ),
    )

    result = fleet_mod.rescue_capture_fleet(config, "sandbox")

    assert result.captured == []
    assert result.deferred == {
        "sandbox-1": "container is not running; nothing to capture"
    }


def test_rescue_capture_fleet_requires_defined_fleet(monkeypatch):
    monkeypatch.setattr(fleet_mod, "_fleet_members", lambda *_args: [])

    with pytest.raises(RuntimeError, match="Fleet 'missing' is not defined"):
        fleet_mod.rescue_capture_fleet(ContainersConfig(), "missing")


def test_restricted_down_uses_safe_per_member_stop(monkeypatch):
    from agent_containers import replacement

    config = ContainersConfig(
        fleets={
            "sandbox": FleetConfig(
                image="example/agent",
                security_profile="restricted",
                acp_command="minimal-agent --stdio",
            )
        }
    )
    members = [
        DockerContainerInfo(
            name=f"sandbox-{index}",
            container_id=f"instance-{index}",
            image="example/agent",
            state="running",
            status="Up",
            fleet="sandbox",
            security_profile="restricted",
        )
        for index in (1, 2)
    ]
    monkeypatch.setattr(fleet_mod, "_fleet_members", lambda *_args: members)
    monkeypatch.setattr(
        fleet_mod,
        "stop_container",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("restricted down must not call unconditional stop")
        ),
    )

    def safe_stop(_config, _fleet, info, **_kwargs):
        if info.name == "sandbox-1":
            return replacement.DestructiveResult(
                info.name,
                "stopped",
                rescue={"status": "verified"},
            )
        return replacement.DestructiveResult(
            info.name,
            "deferred",
            "active Copilot session-state lock present",
        )

    monkeypatch.setattr(replacement, "stop_restricted_member", safe_stop)

    result = fleet_mod.down_fleet(config, "sandbox")

    assert result.stopped == ["sandbox-1"]
    assert result.deferred == {
        "sandbox-2": "active Copilot session-state lock present"
    }


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("docker stop timed out after 30s"),
        RescueError("container generation unavailable"),
    ],
)
def test_restricted_down_timeout_defers_member_and_continues(
    monkeypatch,
    failure,
):
    from agent_containers import replacement

    config = ContainersConfig(
        fleets={
            "sandbox": FleetConfig(
                image="example/agent",
                security_profile="restricted",
                acp_command="minimal-agent --stdio",
            )
        }
    )
    members = [
        DockerContainerInfo(
            name=f"sandbox-{index}",
            container_id=f"instance-{index}",
            image="example/agent",
            state="running",
            status="Up",
            fleet="sandbox",
            security_profile="restricted",
        )
        for index in (1, 2)
    ]
    monkeypatch.setattr(fleet_mod, "_fleet_members", lambda *_args: members)

    def stop(_config, _fleet, info, **_kwargs):
        if info.name == "sandbox-1":
            raise failure
        return replacement.DestructiveResult(info.name, "stopped")

    monkeypatch.setattr(replacement, "stop_restricted_member", stop)

    result = fleet_mod.down_fleet(config, "sandbox")

    assert str(failure) in result.deferred["sandbox-1"]
    assert result.stopped == ["sandbox-2"]


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("docker inspect timed out after 30s"),
        RescueError("rescue pin unavailable"),
    ],
)
def test_restricted_remove_timeout_defers_member_and_continues(
    monkeypatch,
    failure,
):
    from agent_containers import replacement

    config = ContainersConfig(
        fleets={
            "sandbox": FleetConfig(
                image="example/agent",
                security_profile="restricted",
                acp_command="minimal-agent --stdio",
            )
        }
    )
    members = [
        DockerContainerInfo(
            name=f"sandbox-{index}",
            container_id=f"instance-{index}",
            image="example/agent",
            state="running",
            status="Up",
            fleet="sandbox",
            security_profile="restricted",
        )
        for index in (1, 2)
    ]
    monkeypatch.setattr(fleet_mod, "_fleet_members", lambda *_args: members)

    def destroy(_config, _fleet, info, **_kwargs):
        if info.name == "sandbox-1":
            raise failure
        return replacement.DestructiveResult(info.name, "removed")

    monkeypatch.setattr(replacement, "destroy_restricted_member", destroy)

    result = fleet_mod.remove_fleet(config, "sandbox", force=True)

    assert str(failure) in result.deferred["sandbox-1"]
    assert result.removed == ["sandbox-2"]


def test_trusted_down_behavior_remains_direct(monkeypatch):
    config = ContainersConfig(
        fleets={"example": FleetConfig(image="example/agent")}
    )
    member = DockerContainerInfo(
        name="example-1",
        container_id="instance",
        image="example/agent",
        state="running",
        status="Up",
        fleet="example",
        security_profile="trusted",
    )
    monkeypatch.setattr(fleet_mod, "_fleet_members", lambda *_args: [member])
    stopped = []
    monkeypatch.setattr(
        fleet_mod,
        "stop_container",
        lambda name: stopped.append(name),
    )

    result = fleet_mod.down_fleet(config, "example")

    assert result.stopped == ["example-1"]
    assert stopped == ["example-1"]


def test_restricted_down_classifies_stopped_paused_and_unknown_states(monkeypatch):
    from agent_containers import rescue

    config = ContainersConfig(
        fleets={
            "sandbox": FleetConfig(
                image="example/agent",
                security_profile="restricted",
                acp_command="minimal-agent --stdio",
            )
        }
    )
    members = [
        DockerContainerInfo(
            name=f"sandbox-{state}",
            container_id=f"instance-{state}",
            image="example/agent",
            state=state,
            status=state,
            fleet="sandbox",
            security_profile="restricted",
        )
        for state in ("exited", "created", "paused", "restarting", "removing", "unknown")
    ]
    monkeypatch.setattr(fleet_mod, "_fleet_members", lambda *_args: members)
    monkeypatch.setattr(
        fleet_mod,
        "inspect_container",
        lambda name: {
            "Id": name,
            "State": {"StartedAt": "2026-01-01T00:00:00Z"},
        },
    )
    monkeypatch.setattr(
        rescue,
        "verified_capture_for_instance",
        lambda *_args: None,
    )
    losses = []
    monkeypatch.setattr(
        rescue,
        "record_telemetry_loss",
        lambda **kwargs: losses.append(kwargs),
    )

    result = fleet_mod.down_fleet(config, "sandbox")

    assert set(result.unchanged) == {"sandbox-exited", "sandbox-created"}
    assert set(result.deferred) == {
        "sandbox-paused",
        "sandbox-restarting",
        "sandbox-removing",
        "sandbox-unknown",
    }
    assert {item["container"] for item in losses} == {
        "sandbox-exited",
        "sandbox-created",
    }


def test_down_generation_failure_defers_only_affected_stopped_member(monkeypatch):
    from agent_containers import rescue

    config = ContainersConfig(
        fleets={
            "sandbox": FleetConfig(
                image="example/agent",
                security_profile="restricted",
                acp_command="minimal-agent --stdio",
            )
        }
    )
    members = [
        DockerContainerInfo(
            name=f"sandbox-{index}",
            container_id=f"instance-{index}",
            image="example/agent",
            state="exited",
            status="Exited",
            fleet="sandbox",
            security_profile="restricted",
        )
        for index in (1, 2)
    ]
    monkeypatch.setattr(fleet_mod, "_fleet_members", lambda *_args: members)
    monkeypatch.setattr(
        fleet_mod,
        "inspect_container",
        lambda name: {
            "Id": name,
            "State": {"StartedAt": "good" if name.endswith("-2") else ""},
        },
    )
    monkeypatch.setattr(
        rescue,
        "verified_capture_for_instance",
        lambda *_args: {"status": "verified"},
    )

    result = fleet_mod.down_fleet(config, "sandbox")

    assert "execution generation" in result.deferred["sandbox-1"]
    assert "sandbox-2" in result.unchanged




def test_trusted_reconcile_up_recreates_profile_drifted_member(monkeypatch):
    """A fleet relaxed from restricted->trusted should recreate an old member
    still carrying the stale (restricted) discovered profile, not silently
    leave it untouched (copilot-extensions#4933)."""
    from agent_containers import replacement

    config = ContainersConfig()
    config.fleets["worker"] = FleetConfig(
        image="example/agent",
        security_profile="trusted",
    )
    drifted = DockerContainerInfo(
        name="worker-1",
        container_id="old-instance",
        image="example/agent",
        state="running",
        status="Up",
        fleet="worker",
        security_profile="restricted",
    )
    monkeypatch.setattr(fleet_mod, "_check_docker", lambda: None)
    monkeypatch.setattr(fleet_mod, "_fleet_members", lambda *_args: [drifted])
    captured_calls = []

    def fake_destroy_restricted(_config, _fleet, member, **kwargs):
        captured_calls.append((member.name, kwargs.get("migrating")))
        return replacement.DestructiveResult(member.name, "removed")

    monkeypatch.setattr(
        replacement, "destroy_restricted_member", fake_destroy_restricted
    )
    provisioned = []
    monkeypatch.setattr(
        fleet_mod,
        "_image_run",
        lambda _fleet_name, _fleet, name, **_kwargs: (
            provisioned.append(name) or name
        ),
    )

    result = fleet_mod.reconcile_up(config, "worker", recreate=True)

    assert result.removed == ["worker-1"]
    assert result.recreated == ["worker-1"]
    assert provisioned == ["worker-1"]
    # Routed through the full restricted rescue/liveness pipeline, not a
    # lightweight lease-only path.
    assert captured_calls == [("worker-1", True)]


def test_trusted_reconcile_up_without_recreate_raises_on_drift(monkeypatch):
    config = ContainersConfig()
    config.fleets["worker"] = FleetConfig(
        image="example/agent",
        security_profile="trusted",
    )
    drifted = DockerContainerInfo(
        name="worker-1",
        container_id="old-instance",
        image="example/agent",
        state="running",
        status="Up",
        fleet="worker",
        security_profile="restricted",
    )
    monkeypatch.setattr(fleet_mod, "_check_docker", lambda: None)
    monkeypatch.setattr(fleet_mod, "_fleet_members", lambda *_args: [drifted])

    with pytest.raises(RuntimeError, match="security profile"):
        fleet_mod.reconcile_up(config, "worker")


def test_trusted_reconcile_up_defers_drifted_member_with_active_lease(monkeypatch):
    from agent_containers import replacement

    config = ContainersConfig()
    config.fleets["worker"] = FleetConfig(
        image="example/agent",
        security_profile="trusted",
    )
    drifted = DockerContainerInfo(
        name="worker-1",
        container_id="old-instance",
        image="example/agent",
        state="running",
        status="Up",
        fleet="worker",
        security_profile="restricted",
    )
    monkeypatch.setattr(fleet_mod, "_check_docker", lambda: None)
    monkeypatch.setattr(fleet_mod, "_fleet_members", lambda *_args: [drifted])
    monkeypatch.setattr(
        replacement,
        "destroy_restricted_member",
        lambda *_args, **_kwargs: replacement.DestructiveResult(
            "worker-1", "deferred", "container has an active effort lease"
        ),
    )
    monkeypatch.setattr(
        fleet_mod,
        "_image_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("deferred member must not be re-provisioned")
        ),
    )

    result = fleet_mod.reconcile_up(config, "worker", recreate=True)

    assert result.removed == []
    assert result.deferred == {"worker-1": "container has an active effort lease"}


def test_remove_fleet_recreates_drifted_restricted_member(monkeypatch):
    """`rm` on a now-trusted fleet should remove (not hard-defer) a member
    still carrying the stale restricted discovered profile, routed through
    the full restricted rescue/liveness pipeline (migrating=True)."""
    from agent_containers import replacement

    config = ContainersConfig()
    config.fleets["worker"] = FleetConfig(
        image="example/agent",
        security_profile="trusted",
    )
    drifted = DockerContainerInfo(
        name="worker-1",
        container_id="old-instance",
        image="example/agent",
        state="running",
        status="Up",
        fleet="worker",
        security_profile="restricted",
    )
    monkeypatch.setattr(fleet_mod, "_fleet_members", lambda *_args: [drifted])
    captured_calls = []

    def fake_destroy_restricted(_config, _fleet, member, **kwargs):
        captured_calls.append(kwargs.get("migrating"))
        return replacement.DestructiveResult(member.name, "removed")

    monkeypatch.setattr(
        replacement, "destroy_restricted_member", fake_destroy_restricted
    )

    result = fleet_mod.remove_fleet(config, "worker", force=True)

    assert result.removed == ["worker-1"]
    assert result.deferred == {}
    assert captured_calls == [True]
