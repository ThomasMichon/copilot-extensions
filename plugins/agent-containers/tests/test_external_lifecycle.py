"""External managers retain lifecycle custody; session use stays available."""

import asyncio
import json
import os
import subprocess
import sys

import pytest

from agent_containers import config, fleet, lifecycle, resolver
from agent_containers.config import (
    EXTERNAL_LIFECYCLE_OWNER,
    FLEET_LABEL,
    LIFECYCLE_OWNER_LABEL,
    SECURITY_PROFILE_LABEL,
    ContainersConfig,
    FleetConfig,
)
from agent_containers.lifecycle_ownership import (
    lifecycle_blocker,
    pin_lifecycle_command,
    validate_session_owner,
)

IDENTITY = "a" * 64


@pytest.fixture
def external():
    return ContainersConfig(fleets={
        "pilot": FleetConfig(lifecycle_owner="external", image="example:pinned"),
    })


def member(*, owner="external", state="running"):
    return lifecycle.DockerContainerInfo(
        name="pilot-1", container_id=IDENTITY, image="example:pinned",
        state=state, status="", fleet="pilot",
        labels={FLEET_LABEL: "pilot", LIFECYCLE_OWNER_LABEL: owner,
                SECURITY_PROFILE_LABEL: "trusted"},
    )


def record(info):
    return {"Id": info.container_id, "Name": "/" + info.name,
            "Config": {"Labels": info.labels}, "State": {"Status": info.state}}


@pytest.mark.parametrize("owner", [None, False, [], {}, "komodo", "EXTERNAL", ""])
def test_invalid_owner_refused(owner):
    with pytest.raises(RuntimeError, match="lifecycle_owner"):
        FleetConfig(lifecycle_owner=owner).validate_restricted()


def test_restricted_external_refused():
    with pytest.raises(RuntimeError, match="trusted fleet"):
        FleetConfig(lifecycle_owner="external", security_profile="restricted").validate_restricted()


def test_config_loads_external_explicitly(tmp_path, monkeypatch):
    path = tmp_path / "containers.yaml"
    path.write_text("fleets:\n  pilot:\n    lifecycle_owner: external\n")
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(path))
    assert config.load_config(strict=True).fleets["pilot"].lifecycle_owner == "external"
    assert FleetConfig().lifecycle_owner == "agent-containers"


@pytest.mark.parametrize("action", [
    fleet.reconcile_up, fleet.down_fleet, fleet.start, fleet.remove_fleet,
])
def test_external_fleet_commands_refuse_before_effects(external, monkeypatch, action):
    def unexpected(*args, **kwargs):
        pytest.fail("external fleet reached a Docker operation")

    monkeypatch.setattr(fleet, "_check_docker", unexpected)
    monkeypatch.setattr(fleet, "_fleet_members", unexpected)
    with pytest.raises(RuntimeError, match="external lifecycle owner"):
        action(external, "pilot")


@pytest.mark.parametrize("action", [lifecycle.cmd_stop, lifecycle.cmd_remove])
def test_member_frontdoors_preserve_live_external_label(monkeypatch, action):
    info = member()
    monkeypatch.setattr("agent_containers.lease.get_lease", lambda name: None)
    monkeypatch.setattr(lifecycle, "_resolve_member_info", lambda *args: info)
    monkeypatch.setattr(lifecycle, "_docker", lambda *args, **kwargs: pytest.fail("mutation"))
    assert action(ContainersConfig(), info.name) != 0


@pytest.mark.parametrize("action", [fleet.down_fleet, fleet.remove_fleet])
def test_fleet_defers_external_label_after_config_removal(monkeypatch, action):
    monkeypatch.setattr(fleet, "_fleet_members", lambda *args: [member()])
    monkeypatch.setattr(fleet, "stop_container", lambda *args: pytest.fail("stop"))
    monkeypatch.setattr(fleet, "remove_container", lambda *args, **kw: pytest.fail("remove"))
    result = action(ContainersConfig(), "pilot")
    assert "external lifecycle owner" in result.deferred["pilot-1"]


@pytest.mark.parametrize("verb", ["start", "stop", "unpause", "rm"])
@pytest.mark.parametrize("configured", [False, True])
def test_docker_mutation_boundary_observes_ownership(monkeypatch, external, verb, configured):
    selected = external if configured else ContainersConfig()
    info = member(owner="agent-containers" if configured else "external")
    calls = []
    monkeypatch.setattr(config, "load_config", lambda **kwargs: selected)

    def docker(argv, **kwargs):
        calls.append(argv)
        assert argv[1] == "inspect", "external lifecycle effect reached subprocess"
        return subprocess.CompletedProcess(argv, 0, json.dumps([record(info)]), "")

    monkeypatch.setattr(lifecycle.subprocess, "run", docker)
    with pytest.raises(RuntimeError, match="external lifecycle owner"):
        lifecycle._docker([verb, "pilot-1"])
    assert len(calls) == 1


def test_internal_mutation_pins_full_instance_id():
    info = member(owner="agent-containers")
    args = pin_lifecycle_command(["rm", "-f", info.name], ContainersConfig(), lambda _: record(info))
    assert args == ["rm", "-f", IDENTITY]


@pytest.mark.parametrize("identity", [None, "short-id", "b" * 63])
def test_uncertain_instance_never_reaches_mutation(identity):
    observed = record(member(owner="agent-containers"))
    observed["Id"] = identity
    with pytest.raises(RuntimeError, match="identity could not be verified"):
        pin_lifecycle_command(["stop", "pilot-1"], ContainersConfig(), lambda _: observed)


@pytest.mark.parametrize("args", [
    [], ["stop"], ["stop", "other-1", "pilot-1"], ["rm", "-f", "other-1", "pilot-1"],
    ["start", "--all"], ["rm", "--force", "pilot-1"],
])
def test_unverified_extra_targets_or_options_refused(args):
    with pytest.raises(RuntimeError, match="unsupported container lifecycle command"):
        pin_lifecycle_command(
            args, ContainersConfig(), lambda _: pytest.fail("unexpected inspection"),
        )


def test_external_prefix_cannot_borrow_different_label(external):
    assert lifecycle_blocker(external, "pilot-1", {FLEET_LABEL: "other"})


def test_managed_fleet_with_overlapping_prefix_is_not_external(external):
    external.fleets["pilot-tools"] = FleetConfig()
    assert lifecycle_blocker(external, "pilot-tools-1", {FLEET_LABEL: "pilot-tools"}) is None


@pytest.mark.parametrize("name", ["pilot-dev", "pilot-dev-one"])
@pytest.mark.parametrize("action", [lifecycle.cmd_stop, lifecycle.cmd_remove])
def test_unlabeled_nonnumeric_external_member_frontdoors_refuse(
    external, monkeypatch, name, action,
):
    info = member()
    info.name = name
    info.fleet = None
    info.labels = {SECURITY_PROFILE_LABEL: "trusted"}
    monkeypatch.setattr("agent_containers.lease.get_lease", lambda name: None)
    monkeypatch.setattr(lifecycle, "_resolve_member_info", lambda *args: info)
    monkeypatch.setattr(lifecycle, "_docker", lambda *args, **kwargs: pytest.fail("mutation"))
    assert action(external, name) != 0


@pytest.mark.parametrize("name", ["pilot-dev", "pilot-dev-one"])
@pytest.mark.parametrize("verb", ["start", "stop", "unpause", "rm"])
def test_unlabeled_nonnumeric_external_member_mutations_refuse(external, name, verb):
    info = member()
    info.name = name
    info.fleet = None
    info.labels = {SECURITY_PROFILE_LABEL: "trusted"}
    with pytest.raises(RuntimeError, match="external lifecycle owner"):
        pin_lifecycle_command([verb, name], external, lambda _: record(info))


@pytest.mark.parametrize(("configured", "observed"), [
    ("external", "agent-containers"), ("agent-containers", "external"),
])
def test_namespace_refuses_owner_mismatch_before_ssh(monkeypatch, configured, observed):
    info = member(owner=observed)
    selected = ContainersConfig(fleets={"pilot": FleetConfig(lifecycle_owner=configured)})
    monkeypatch.setattr(resolver, "load_config", lambda: selected)
    monkeypatch.setattr(resolver, "get_container", lambda *args: info)
    monkeypatch.setattr(lifecycle, "inspect_container", lambda *args: record(info))
    monkeypatch.setattr(resolver, "prepare_ssh_config", lambda *a: pytest.fail("SSH projection"))
    monkeypatch.setattr(resolver, "get_lease", lambda *args: None)
    monkeypatch.setattr(resolver, "payload_command_argv", lambda: ["agent-containers"])
    with pytest.raises(RuntimeError, match="lifecycle owner does not match"):
        asyncio.run(resolver.ContainerResolver().resolve_spec(info.name))


@pytest.mark.parametrize("state", ["running", "exited"])
def test_external_readiness_never_starts(external, monkeypatch, state):
    info = member(state=state)
    monkeypatch.setattr(resolver, "load_config", lambda: external)
    monkeypatch.setattr(resolver, "get_container", lambda *args: info)
    monkeypatch.setattr(resolver, "inspect_state", lambda *args: state)
    monkeypatch.setattr(resolver, "start_container", lambda *args: pytest.fail("implicit start"))
    if state == "running":
        asyncio.run(resolver.ContainerResolver().ensure_ready(info.name))
    else:
        with pytest.raises(RuntimeError, match="external lifecycle owner"):
            asyncio.run(resolver.ContainerResolver().ensure_ready(info.name))


def test_external_session_requires_matching_live_label(external):
    selected = external.fleets["pilot"]
    validate_session_owner(selected, {LIFECYCLE_OWNER_LABEL: EXTERNAL_LIFECYCLE_OWNER})
    with pytest.raises(RuntimeError, match="owner does not match"):
        validate_session_owner(selected, {})


def test_recreation_refuses_before_removal(external, monkeypatch):
    monkeypatch.setattr(fleet, "_check_docker", lambda: None)
    monkeypatch.setattr(fleet, "get_container", lambda *args: member())
    monkeypatch.setattr(fleet, "remove_container", lambda *args, **kw: pytest.fail("remove"))
    with pytest.raises(RuntimeError, match="external lifecycle owner"):
        fleet.recreate_member(external, "pilot-1", expected_container_id=IDENTITY)


def test_real_cli_refuses_external_creation_without_docker(tmp_path):
    path = tmp_path / "containers.yaml"
    path.write_text("fleets:\n  pilot:\n    lifecycle_owner: external\n")
    environment = dict(os.environ, AGENT_CONTAINERS_CONFIG=str(path))
    result = subprocess.run(
        [sys.executable, "-m", "agent_containers", "up", "pilot"],
        capture_output=True, text=True, timeout=30, env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode != 0
    assert "external lifecycle owner" in result.stderr


def test_running_external_member_resolves_for_execution(external, monkeypatch):
    info = member()
    monkeypatch.setattr(lifecycle, "get_container", lambda *args: info)
    monkeypatch.setattr(lifecycle, "inspect_container", lambda *args: record(info))
    monkeypatch.setattr(resolver, "get_lease", lambda *args: None)
    target = resolver.resolve_live_exec_target(info.name, config=external)
    assert target.container_id == IDENTITY
    assert target.actual_profile == "trusted"


def test_creation_helpers_refuse_external_mode(external, monkeypatch):
    from agent_containers import devcontainer_launch

    selected = external.fleets["pilot"]
    monkeypatch.setattr(fleet, "_docker", lambda *a, **kw: pytest.fail("image run"))
    monkeypatch.setattr(devcontainer_launch.shutil, "which", lambda *a: pytest.fail("CLI lookup"))
    with pytest.raises(RuntimeError, match="external lifecycle owner"):
        fleet._image_run("pilot", selected, "pilot-1",
                         workspace_folder="/workspaces", exec_user="vscode")
    with pytest.raises(RuntimeError, match="external lifecycle owner"):
        devcontainer_launch._devcontainer_up("pilot", selected, "pilot-1")
