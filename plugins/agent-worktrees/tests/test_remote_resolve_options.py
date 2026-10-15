"""Remote resolve preserves options or refuses before emitting a launch."""

from __future__ import annotations

import argparse
import base64
import shlex
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_worktrees import config as cfg, remote_seed_launch, resolve_cli, resolve_machine_cli

pytestmark = pytest.mark.guard


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    resolve_cli.add_parsers(parser.add_subparsers())
    return parser.parse_args(["resolve", *argv])


@pytest.fixture
def remote(monkeypatch: pytest.MonkeyPatch, tmp_path):
    config = cfg.Config(
        machine="local-host", platform="windows", repo_name="example", srcroot=str(tmp_path),
        repos={"example": cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path / "trees"))},
    )
    entry = cfg.MachineEntry(
        key="remote-host", display_name="Remote host", environment="Linux", ssh_ready=True,
        ssh_environments=[cfg.SSHEnvironment("wsl", "guest profile", shell="bash")],
    )
    monkeypatch.setattr(cfg, "load_config", lambda *a, **kw: config)
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *a, **kw: {entry.key: entry})
    monkeypatch.setattr(cfg, "project_name", lambda: "example")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(resolve_cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(resolve_machine_cli, "_in_ssh_session", lambda: False)
    plans: list[dict] = []
    monkeypatch.setattr(resolve_machine_cli, "_emit_plan", plans.append)
    return config, entry, plans


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("environment", [None, "WSL"])
@pytest.mark.parametrize("selector", [
    ["--worktree-id", "worktree id"], ["--new"], ["--base"],
])
def test_remote_plan_preserves_supported_options_and_quotes_values(
    remote, json_mode: bool, environment: str | None, selector: list[str],
) -> None:
    _, _, plans = remote
    options = [
        "--no-resume", "--no-fast-forward", "--recovery", "--defer-projections",
        "--profile", "profile name 'quoted'; $value",
        "--parent-session", "parent session", "--caller-worktree", "caller (Windows)/project/id",
        "--bridge", "--defer-new-seed", "--stage-launch-seed",
    ]
    if selector[0] == "--worktree-id":
        options += ["--bare-resume", "--restore", "--dry-run"]
    args = _parse([
        "--machine", "remote-host", *selector, *options,
        *(["--environment", environment] if environment else []),
        *(["--json", "--target-no-mux"] if json_mode else ["--no-mux"]),
    ])
    assert resolve_cli.cmd_resolve(args) == 0
    plan, = plans
    assert plan["ssh_alias"] == "guest profile"
    shell, login, command = shlex.split(plan["remote_command"])
    assert (shell, login) == ("bash", "-lc")
    argv = shlex.split(command)
    assert argv[0] == "example"
    forwarded = _parse(argv[1:])
    for field in (
        "no_resume", "no_fast_forward", "recovery", "defer_projections",
        "profile", "parent_session", "caller_worktree", "bridge", "defer_new_seed",
        "stage_launch_seed", "worktree_id", "new_worktree", "base",
        "bare_resume", "restore", "dry_run",
    ):
        assert getattr(forwarded, field) == getattr(args, field), field
    assert forwarded.no_mux


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("environment", [None, "WSL"])
@pytest.mark.parametrize("unsupported", [
    ["--owner-ref", "Example Host/project/parent#session"],
    ["--", "raw argument"],
])
def test_remote_request_refuses_unrepresentable_ownership_or_raw_args(
    remote, capfd, json_mode: bool, environment: str | None, unsupported: list[str],
) -> None:
    _, _, plans = remote
    args = _parse([
        "--machine", "remote-host", "--new",
        *(["--environment", environment] if environment else []),
        *(["--json"] if json_mode else []), *unsupported,
    ])
    assert resolve_cli.cmd_resolve(args) != 0
    assert not plans
    captured = capfd.readouterr()
    assert "unsupported" in captured.out + captured.err


@pytest.mark.parametrize("requested", [False, True])
def test_json_internal_no_mux_is_not_a_remote_override(requested: bool) -> None:
    state = resolve_cli.ResolveCommandState.from_args(_parse([
        "--json", "--new", "--machine", "remote-host",
        *(["--target-no-mux"] if requested else []),
    ]))
    assert state.args.no_mux
    assert ("--no-mux" in resolve_cli._remote_selector_args(state)) is requested


@pytest.mark.parametrize("option", ["--launch-request-b64", "--launch-request-status"])
def test_target_local_request_protocol_refuses_machine_dispatch(
    monkeypatch: pytest.MonkeyPatch, capfd, option: str,
) -> None:
    from agent_worktrees import launch_request

    def forbidden(*args, **kwargs):
        pytest.fail("target-local protocol reached local admission with a machine target")

    monkeypatch.setattr(launch_request, "cmd_request", forbidden)
    assert resolve_cli.cmd_resolve(_parse([
        "--json", "--machine", "remote-host", option, "opaque-request",
    ])) != 0
    assert "cannot be combined" in capfd.readouterr().out


def test_remote_profile_starting_with_flag_prefix_round_trips() -> None:
    state = resolve_cli.ResolveCommandState.from_args(_parse([
        "--json", "--new", "--machine", "remote-host", "--profile=--special profile",
    ]))
    assert _parse(resolve_cli._remote_selector_args(state)).profile == "--special profile"


@pytest.mark.parametrize("options", [
    ["--no-resume"], ["--no-fast-forward"], ["--profile", "profile name"],
    ["--recovery"], ["--parent-session", "parent"], ["--caller-worktree", "caller"],
])
def test_json_seed_route_refuses_unsupported_intent_without_ssh(
    remote, monkeypatch: pytest.MonkeyPatch, capfd, options: list[str],
) -> None:
    _, _, plans = remote

    def forbidden(*args, **kwargs):
        pytest.fail("unsupported seed intent reached SSH")

    monkeypatch.setattr(remote_seed_launch.subprocess, "run", forbidden)
    args = _parse([
        "--json", "--machine", "remote-host", "--environment", "WSL",
        "--new", "--seed", "task", *options,
    ])
    assert resolve_cli.cmd_resolve(args) != 0
    assert not plans
    assert "does not support" in capfd.readouterr().out


@pytest.mark.parametrize("flag", [
    "--no-resume", "--no-fast-forward", "--recovery", "--profile",
    "--parent-session", "--caller-worktree", "--bridge", "--defer-projections", "--dry-run",
])
def test_structured_seed_refuses_unsupported_options_before_admission(
    monkeypatch: pytest.MonkeyPatch, flag: str,
) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("unsupported prompt options reached remote admission")

    monkeypatch.setattr(remote_seed_launch.subprocess, "run", forbidden)
    with pytest.raises(ValueError, match="does not support"):
        remote_seed_launch.prepare(None, "guest", "bash", ["--new", flag], "task")


def test_remote_option_inventory_covers_all_resolve_parser_destinations() -> None:
    # New options must join the forwarding/refusal policy rather than disappear.
    assert set(vars(_parse([]))) == {
        "dry_run", "recovery", "defer_projections", "no_resume", "bare_resume", "restore",
        "no_mux", "no_fast_forward", "json", "worktree_id", "codename", "base", "auto",
        "new_worktree", "bridge", "profile", "defer_new_seed", "stage_launch_seed",
        "seed_id", "launch_request_b64", "launch_request_status", "machine", "environment",
        "target_no_mux", "parent_session", "caller_worktree", "owner_ref", "seed", "copilot_args",
    }


def test_powershell_remote_plan_quotes_each_value(remote) -> None:
    config, entry, plans = remote
    entry.ssh_environments[0] = replace(entry.ssh_environments[0], shell="pwsh")
    value = "profile with 'quotes'; $value"
    assert resolve_machine_cli._emit_remote_plan_for_env(
        config, entry.key, "WSL", ["--profile", value, "--no-resume", "--no-fast-forward"],
    ) == 0
    plan, = plans
    script = base64.b64decode(plan["remote_command"].rsplit(" ", 1)[1]).decode("utf-16le")
    assert script == (
        "& 'example' '--profile' 'profile with ''quotes''; $value' "
        "'--no-resume' '--no-fast-forward'; exit $LASTEXITCODE"
    )
