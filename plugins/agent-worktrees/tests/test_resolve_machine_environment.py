"""Launch boundaries accept physical host/environment and logical WSL keys."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_worktrees import config as cfg, resolve_cli, resolve_machine_cli

pytestmark = pytest.mark.guard


def _entries() -> dict[str, cfg.MachineEntry]:
    native = cfg.MachineEntry(
        key="example-host", display_name="Example host", alias="native-label",
        execution_platform="windows",
        ssh_environments=[cfg.SSHEnvironment("windows", "native-ssh")],
    )
    guest = cfg.MachineEntry(
        key="example-host-wsl", display_name="Example guest",
        execution_platform="wsl",
        ssh_environments=[cfg.SSHEnvironment("wsl", "guest-ssh")],
    )
    return {entry.key: entry for entry in (native, guest)}


@pytest.mark.parametrize(("name", "environment"), [
    ("Example-Host", "WSL"), ("example-host-wsl", "WSL"),
    ("EXAMPLE-HOST-WSL", None), ("native-label", "WSL"),
])
def test_tuple_and_logical_key_select_the_same_guest(name, environment) -> None:
    entries = _entries()
    assert resolve_machine_cli._entry_for_environment(entries, name, environment) is (
        entries["example-host-wsl"]
    )


def test_unsplit_legacy_host_retains_its_environment_transport() -> None:
    entries = _entries()
    legacy = replace(
        entries["example-host"], execution_platform="",
        ssh_environments=[
            *entries["example-host"].ssh_environments,
            *entries["example-host-wsl"].ssh_environments,
        ],
    )
    assert resolve_machine_cli._entry_for_environment(
        {legacy.key: legacy}, "example-host", "WSL",
    ) is legacy


def test_native_target_does_not_select_a_guest() -> None:
    entries = _entries()
    assert resolve_machine_cli._entry_for_environment(
        entries, "example-host", "Win",
    ) is entries["example-host"]
    with pytest.raises(ValueError, match="conflicts"):
        resolve_machine_cli._entry_for_environment(entries, "example-host-wsl", "Win")
    assert resolve_machine_cli._entry_for_environment(
        {"example-host": entries["example-host"]}, "example-host", "WSL",
    ) is None


@pytest.mark.parametrize("configured", ["example-host", "example-host-wsl"])
@pytest.mark.parametrize("name", ["Example-Host", "example-host-wsl"])
def test_same_wsl_tuple_is_local_with_legacy_or_logical_configuration(
    monkeypatch, configured: str, name: str,
) -> None:
    config = SimpleNamespace(machine=configured)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    monkeypatch.setattr(
        resolve_cli.machine_identity, "is_local_machine",
        lambda *_args: pytest.fail("An identical execution tuple must not require registry lookup"),
    )
    assert resolve_cli._target_is_local(config, name, "WSL")


def test_native_side_dispatches_tuple_to_guest_transport(monkeypatch) -> None:
    config = SimpleNamespace(
        machine="example-host", default_repo=SimpleNamespace(anchor="example-repo"),
    )
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *_args: _entries())
    monkeypatch.setattr(cfg, "project_name", lambda: "example-project")
    monkeypatch.setattr(
        resolve_cli.machine_identity, "is_local_machine",
        lambda name, _config: name == "example-host",
    )
    assert not resolve_cli._target_is_local(config, "Example-Host", "WSL")
    plans = []
    monkeypatch.setattr(resolve_machine_cli, "_emit_plan", plans.append)
    assert resolve_machine_cli._emit_remote_plan_for_env(
        config, "Example-Host", "WSL", ["--worktree-id", "example-worktree"],
    ) == 0
    assert plans[0]["machine"] == "example-host-wsl"
    assert plans[0]["ssh_alias"] == "guest-ssh"
    assert "--worktree-id example-worktree" in plans[0]["remote_command"]


@pytest.mark.parametrize(("name", "environment"), [
    ("Example-Host", "WSL"), ("example-host-wsl", None),
])
def test_json_creation_uses_local_allocator_for_the_same_wsl_execution_tuple(
    monkeypatch, capfd, name: str, environment: str | None,
) -> None:
    config = SimpleNamespace(machine="example-host-wsl")
    state = resolve_cli.ResolveCommandState.from_args(SimpleNamespace(
        json=True, new_worktree=True, machine=name, environment=environment,
        worktree_id=None,
    ))
    state.config = config
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    monkeypatch.setattr(resolve_cli, "_validate_profile_assignment_config", lambda *_args: None)
    monkeypatch.setattr(resolve_cli, "_resolve_profile", lambda *_args: None)
    allocated = []

    def create(local_config, **kwargs):
        allocated.append(local_config)
        return {"action": "exec"}

    monkeypatch.setattr(resolve_cli, "_create_worktree_core", create)
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *_args: pytest.fail("Same-environment creation must not use SSH"),
    )
    assert resolve_cli._resolve_json_mode(state) == 0
    assert allocated == [config]
