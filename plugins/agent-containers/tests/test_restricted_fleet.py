"""Restricted-fleet transport boundary tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_containers.config import ContainersConfig, FleetConfig
from agent_containers import fleet as fleet_mod
from agent_containers.lifecycle import DockerContainerInfo, restricted_policy_errors


def _ok(stdout: str = ""):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


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
    tmpfs = [run[i + 1] for i, value in enumerate(run) if value == "--tmpfs"]
    assert any(value.startswith("/workspace:") for value in tmpfs)
    assert any(value.startswith("/home/vscode:") for value in tmpfs)
    assert "HOME=/home/vscode" in run
    assert "MODEL_NAME=local-model" in run
    assert "--add-host=host.docker.internal:host-gateway" not in run


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
        devcontainer_path="/tmp/spec",
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
                "/tmp": "rw,nosuid,nodev,size=512m",
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
