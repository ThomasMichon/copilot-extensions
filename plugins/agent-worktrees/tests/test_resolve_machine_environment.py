"""Launch boundaries accept physical host/environment and logical WSL keys."""

from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from agent_worktrees import config as cfg, resolve_cli, resolve_machine_cli

pytestmark = pytest.mark.guard


@pytest.mark.parametrize(("name", "environment"), [
    ("example-host-wsl", None), ("example-host", "WSL"),
    ("generated-host", "WSL"), ("native-label", "WSL"),
])
def test_host_only_legacy_topology_preserves_local_wsl_launch(
    monkeypatch, name: str, environment: str | None,
) -> None:
    config = SimpleNamespace(
        machine="example-host-wsl", default_repo=SimpleNamespace(anchor="example-repo"),
    )
    host = cfg.MachineEntry(
        key="example-host", display_name="Example", hostname="generated-host", alias="native-label",
    )
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *_args, **_kwargs: {host.key: host})
    assert resolve_cli._target_is_local(config, name, environment)


@pytest.mark.parametrize("label", ["Win", "Windows", " Windows "])
def test_json_native_labels_share_normalization(monkeypatch, label: str) -> None:
    config = SimpleNamespace(machine="example-host")
    state = resolve_cli.ResolveCommandState.from_args(SimpleNamespace(
        json=True, new_worktree=True, machine="example-host", environment=label,
        worktree_id=None,
    ))
    state.config = config
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(resolve_cli, "_validate_profile_assignment_config", lambda *_args: None)
    monkeypatch.setattr(resolve_cli, "_resolve_profile", lambda *_args: None)
    monkeypatch.setattr(resolve_cli, "_create_worktree_core", lambda *_args, **_kwargs: {"action": "exec"})
    assert resolve_cli._resolve_json_mode(state) == 0


def test_remote_selector_preserves_resume_actions() -> None:
    state = resolve_cli.ResolveCommandState.from_args(SimpleNamespace(
        json=False, worktree_id="example-worktree", machine="remote-host",
        bare_resume=True, restore=True, no_mux=True, dry_run=True,
    ))
    assert resolve_cli._remote_selector_args(state) == [
        "--worktree-id", "example-worktree", "--bare-resume", "--restore", "--no-mux", "--dry-run",
    ]


def test_json_route_reports_execution_authority_error_without_starting_a_launch(monkeypatch, capfd):
    from agent_worktrees.execution_spaces import ExecutionSpaceError

    state = resolve_cli.ResolveCommandState.from_args(SimpleNamespace(
        json=True, new_worktree=True, machine="example-host", environment="WSL", worktree_id=None,
    ))
    state.config = SimpleNamespace(machine="example-host-wsl")

    def invalid(*_args, **_kwargs):
        raise ExecutionSpaceError("fixture registered authority is invalid")

    monkeypatch.setattr(resolve_cli, "_target_is_local", invalid)
    assert resolve_cli._resolve_json_mode(state) == 1
    assert json.loads(capfd.readouterr().out)["error"] == "fixture registered authority is invalid"


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
    ("EXAMPLE-HOST-WSL", None),
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
    with pytest.raises(ValueError, match="conflicts"):
        resolve_machine_cli._entry_for_environment(entries, "native-label", "WSL")
    assert resolve_machine_cli._entry_for_environment(
        entries, "example-host", "Win",
    ) is entries["example-host"]
    with pytest.raises(ValueError, match="conflicts"):
        resolve_machine_cli._entry_for_environment(entries, "example-host-wsl", "Win")
    with pytest.raises(ValueError, match="no wsl logical entry"):
        resolve_machine_cli._entry_for_environment(
            {"example-host": entries["example-host"]}, "example-host", "WSL",
        )


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
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *_args, **_kwargs: _entries())
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


def test_explicit_association_precedes_lexical_locality_and_allocator(monkeypatch):
    entries = _entries()
    entries["example-host-wsl"] = replace(
        entries["example-host-wsl"], physical_host="another-host",
    )
    entries["custom-guest"] = cfg.MachineEntry(
        key="custom-guest", display_name="Custom guest", execution_platform="wsl",
        physical_host="example-host", ssh_environments=[cfg.SSHEnvironment("wsl", "custom-ssh")],
    )
    config = cfg.Config(
        srcroot="", machine="example-host-wsl", platform="wsl", repo_name="example",
        repos={"example": cfg.RepoConfig(anchor="example-repo", worktree_root="")},
    )
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *_args, **_kwargs: entries)
    monkeypatch.setattr(cfg, "project_name", lambda: "example")
    assert not resolve_cli._target_is_local(config, "example-host", "WSL")
    monkeypatch.setattr(
        resolve_cli, "_create_worktree_core",
        lambda *_args, **_kwargs: pytest.fail("a registered foreign guest cannot use the local allocator"),
    )
    state = resolve_cli.ResolveCommandState.from_args(SimpleNamespace(
        json=True, new_worktree=True, machine="example-host", environment="WSL", worktree_id=None,
    ))
    state.config = config
    plans = []
    monkeypatch.setattr(resolve_machine_cli, "_emit_plan", plans.append)
    assert resolve_cli._resolve_json_mode(state) == 0
    assert plans[0]["machine"] == "custom-guest"
    assert plans[0]["ssh_alias"] == "custom-ssh"


@pytest.mark.parametrize(("name", "environment"), [
    ("generated-host", "WSL"), ("generated-host-wsl", None),
])
def test_legacy_hostname_guest_launch_uses_independent_profile(monkeypatch, name, environment):
    entries = {
        "example-host": cfg.MachineEntry(
            key="example-host", display_name="Host", hostname="generated-host",
            ssh_environments=[cfg.SSHEnvironment("windows", "native-ssh")],
        ),
        "example-host-wsl": cfg.MachineEntry(
            key="example-host-wsl", display_name="Guest", hostname="generated-host",
            ssh_environments=[cfg.SSHEnvironment("wsl", "guest-ssh")],
        ),
    }
    config = SimpleNamespace(default_repo=SimpleNamespace(anchor="example-repo"))
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *_args: entries)
    monkeypatch.setattr(cfg, "project_name", lambda: "example")
    plans = []
    monkeypatch.setattr(resolve_machine_cli, "_emit_plan", plans.append)
    assert resolve_machine_cli._emit_remote_plan_for_env(config, name, environment) == 0
    assert plans[0]["machine"] == "example-host-wsl"
    assert plans[0]["ssh_alias"] == "guest-ssh"


@pytest.mark.parametrize("surface", [
    "json", "interactive", "interactive-new", "interactive-resume", "interactive-preview",
])
@pytest.mark.parametrize("explicit_environment", [False, True])
def test_suffix_only_guest_never_launches_locally_on_native_host(
    monkeypatch, surface, explicit_environment,
):
    config = cfg.Config(
        srcroot="", machine="generated-host", platform="windows", repo_name="example",
        repos={"example": cfg.RepoConfig(anchor="example-repo", worktree_root="")},
    )
    native = cfg.MachineEntry(
        key="example-host", display_name="Host", hostname="generated-host", environment="Windows",
        ssh_environments=[
            cfg.SSHEnvironment("windows", "native-ssh"), cfg.SSHEnvironment("wsl", "guest-ssh"),
        ],
    )
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *_args, **_kwargs: {native.key: native})
    monkeypatch.setattr(cfg, "load_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(cfg, "project_name", lambda: "example")
    monkeypatch.setattr(
        resolve_cli, "_create_worktree_core",
        lambda *_args, **_kwargs: pytest.fail("a native process cannot allocate a guest checkout"),
    )
    monkeypatch.setattr(
        resolve_cli, "_try_machine_handoff",
        lambda *_args, **_kwargs: pytest.fail("suffix-only selectors need environment-aware routing"),
    )
    monkeypatch.setattr(
        resolve_cli, "_resolve_new",
        lambda *_args, **_kwargs: pytest.fail("a foreign guest cannot use local creation"),
    )
    monkeypatch.setattr(
        resolve_cli, "_resolve_noninteractive_worktree",
        lambda *_args, **_kwargs: pytest.fail("a foreign guest cannot use local resume"),
    )
    monkeypatch.setattr(resolve_cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(resolve_machine_cli, "_in_ssh_session", lambda: False)
    args = SimpleNamespace(
        json=surface == "json", new_worktree=surface in ("interactive-new", "interactive-preview"),
        machine="example-host" if explicit_environment else "example-host-wsl",
        environment="WSL" if explicit_environment else None,
        worktree_id="wt-guest" if surface in ("json", "interactive-resume") else None,
        dry_run=surface == "interactive-preview",
    )
    plans = []
    monkeypatch.setattr(resolve_machine_cli, "_emit_plan", plans.append)
    assert resolve_cli.cmd_resolve(args) == 0
    assert plans[0]["ssh_alias"] == "guest-ssh"
    if surface == "interactive-preview":
        assert "--new" in plans[0]["remote_command"]
        assert "--dry-run" in plans[0]["remote_command"]


@pytest.mark.parametrize(("name", "environment"), [
    ("Example-Host", "WSL"), ("example-host-wsl", None),
])
def test_json_creation_uses_local_allocator_for_the_same_wsl_execution_tuple(
    monkeypatch, capfd, name: str, environment: str | None,
) -> None:
    config = SimpleNamespace(
        machine="example-host-wsl", default_repo=SimpleNamespace(anchor="example-repo"),
    )
    state = resolve_cli.ResolveCommandState.from_args(SimpleNamespace(
        json=True, new_worktree=True, machine=name, environment=environment,
        worktree_id=None,
    ))
    state.config = config
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *_args, **_kwargs: {})
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
