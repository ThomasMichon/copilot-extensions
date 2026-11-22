"""Remote resolve preserves options or refuses before emitting a launch."""

from __future__ import annotations

import argparse
import base64
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn

import pytest
from machine_transport import MachineEntry, SSHEnvironment

from agent_worktrees import config as cfg, remote_seed_launch, resolve_cli, resolve_machine_cli

pytestmark = pytest.mark.guard
Remote = tuple[cfg.Config, MachineEntry, list[dict[str, Any]]]


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell unavailable")
def test_terminal_powershell_seed_reaches_the_normal_target_parser_literally() -> None:
    from agent_worktrees import remote_terminal_launch

    seed = "--literal 'quotes' \"double\"\nUnicode \u03bb; $(not-a-command) `newline"
    plan = remote_terminal_launch.plan(
        "example", "target", "pwsh", ["--new", "--no-mux"], seed=seed,
    )
    command = base64.b64decode(plan["remote_command"].rsplit(" ", 1)[1]).decode("utf-16le")
    driver = (
        "function example { ConvertTo-Json -Compress -EscapeHandling EscapeNonAscii -InputObject @($args); "
        "$global:LASTEXITCODE=0 }\n" + command
    )
    pwsh = shutil.which("pwsh")
    assert pwsh is not None
    completed = subprocess.run(
        [pwsh, "-NoProfile", "-Command", driver],
        capture_output=True, text=True, encoding="utf-8", timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    assert completed.returncode == 0, completed.stderr
    target = _parse(json.loads(completed.stdout))
    assert target.new_worktree and target.no_mux and target.seed == seed
    assert target.require_foreground_completion


@pytest.mark.parametrize("selector,seed", [
    (["--new"], "quotes ' \"\nUnicode \u03bb; $(not-a-command)"),
    (["--worktree-id", "worktree id"], "--literal-prompt"),
    (["--base"], None),
])
def test_terminal_handoff_packages_normal_target_command_without_contact(
    remote: Remote, monkeypatch: pytest.MonkeyPatch, selector: list[str], seed: str | None,
) -> None:
    _, _, plans = remote
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: pytest.fail("remote work ran before terminal handoff"),
    )
    args = _parse([
        "--json", "--machine", "remote-host", "--environment", "WSL",
        "--require-terminal-handoff", "--target-no-mux", *selector,
        "--profile", "named profile", "--no-resume",
        *(["--seed=" + seed, "--require-native-seed"] if seed else []),
    ])
    assert resolve_cli.cmd_resolve(args) == 0
    plan, = plans
    assert plan["remote_terminal_handoff_version"] == 1
    assert "launch_request_id" not in plan and "seed_id" not in plan
    shell, login, command = shlex.split(plan["remote_command"])
    assert (shell, login) == ("bash", "-lc")
    target = shlex.split(command)
    assert target.pop(0) == "example"
    forwarded = _parse(target)
    assert forwarded.seed == seed
    assert forwarded.no_mux and forwarded.no_resume and forwarded.profile == "named profile"
    assert forwarded.new_worktree == args.new_worktree
    assert forwarded.base == args.base and forwarded.worktree_id == args.worktree_id
    assert forwarded.machine is None and not forwarded.require_terminal_handoff
    assert forwarded.require_foreground_completion


def test_terminal_handoff_refuses_recovery_before_contact_or_plan_emission(
    remote: Remote, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
) -> None:
    _, _, plans = remote

    def forbidden(*args: Any, **kwargs: Any) -> NoReturn:
        pytest.fail("recovery reached a subprocess before terminal handoff refusal")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    args = _parse([
        "--json", "--machine", "remote-host", "--environment", "WSL",
        "--require-terminal-handoff", "--new", "--recovery",
    ])
    assert resolve_cli.cmd_resolve(args) != 0
    assert not plans
    captured = capfd.readouterr()
    assert "recovery" in (captured.out + captured.err).casefold()


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    resolve_cli.add_parsers(parser.add_subparsers())
    return parser.parse_args(["resolve", *argv])


@pytest.fixture
def remote(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Remote:
    config = cfg.Config(
        machine="local-host", platform="windows", repo_name="example", srcroot=str(tmp_path),
        repos={"example": cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path / "trees"))},
    )
    entry = MachineEntry(
        key="remote-host", display_name="Remote host", environment="Linux", ssh_ready=True,
        ssh_environments=[SSHEnvironment("wsl", "guest profile", shell="bash")],
    )
    monkeypatch.setattr(cfg, "load_config", lambda *a, **kw: config)
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *a, **kw: {entry.key: entry})
    monkeypatch.setattr(cfg, "project_name", lambda: "example")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(resolve_machine_cli, "_in_ssh_session", lambda: False)
    plans: list[dict[str, Any]] = []
    monkeypatch.setattr(resolve_machine_cli, "_emit_plan", plans.append)
    return config, entry, plans


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("environment", [None, "WSL"])
@pytest.mark.parametrize("selector", [
    ["--worktree-id", "worktree id"], ["--new"], ["--base"],
])
def test_remote_plan_preserves_supported_options_and_quotes_values(
    remote: Remote, json_mode: bool, environment: str | None, selector: list[str],
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
    assert "agent-policy" in command and "--require-agent-hosting" not in command
    argv = shlex.split(command.split(" && ", 1)[1])
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


@pytest.mark.parametrize("target", ["remote-host", "local-host"])
@pytest.mark.parametrize("selector", [["--new"], ["--worktree-id", "wt-a"]])
def test_native_seed_requirement_reaches_only_structured_remote_admission(
    remote: Remote, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
    target: str, selector: list[str],
) -> None:
    _, entry, plans = remote
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *args, **kwargs: {
        entry.key: entry, "local-host": replace(entry, key="local-host"),
    })
    calls: list[tuple[list[str], str]] = []
    def prepare(
        config: cfg.Config, alias: str, shell: str, args: list[str], text: str,
    ) -> dict[str, Any]:
        calls.append((args, text))
        return {"action": "remote"}
    monkeypatch.setattr(remote_seed_launch, "prepare", prepare)
    args = _parse([
        "--json", "--machine", target, "--environment", "WSL", *selector,
        "--seed", "retained intent", "--stage-launch-seed", "--require-native-seed",
    ])
    assert resolve_cli.cmd_resolve(args) == 0
    assert len(plans) == len(calls) == 1
    assert "--require-native-seed" in calls[0][0]
    assert calls[0][1] == "retained intent"


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("environment", [None, "WSL"])
@pytest.mark.parametrize("unsupported", [
    ["--owner-ref", "Example Host/project/parent#session"],
    ["--", "raw argument"],
])
def test_remote_request_refuses_unrepresentable_ownership_or_raw_args(
    remote: Remote, capfd: pytest.CaptureFixture[str], json_mode: bool,
    environment: str | None, unsupported: list[str],
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
    assert ("--no-mux" in resolve_machine_cli.remote_selector_args(state)) is requested


@pytest.mark.parametrize("option", ["--launch-request-b64", "--launch-request-status"])
def test_target_local_request_protocol_refuses_machine_dispatch(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], option: str,
) -> None:
    from agent_worktrees import launch_request

    def forbidden(*args: Any, **kwargs: Any) -> NoReturn:
        pytest.fail("target-local protocol reached admission, status or machine resolution")

    monkeypatch.setattr(launch_request, "execute", forbidden)
    monkeypatch.setattr(launch_request, "inspect", forbidden)
    monkeypatch.setattr(resolve_cli.ResolveCommandState, "from_args", forbidden)
    assert resolve_cli.cmd_resolve(_parse([
        "--json", "--machine", "remote-host", option, "opaque-request",
    ])) == 3
    assert "Structured launch requests require" in json.loads(capfd.readouterr().out)["error"]


def test_remote_profile_starting_with_flag_prefix_round_trips() -> None:
    state = resolve_cli.ResolveCommandState.from_args(_parse([
        "--json", "--new", "--machine", "remote-host", "--profile=--special profile",
    ]))
    assert _parse(resolve_machine_cli.remote_selector_args(state)).profile == "--special profile"


@pytest.mark.parametrize("options", [
    ["--no-resume"], ["--no-fast-forward"], ["--profile", "profile name"],
    ["--recovery"], ["--parent-session", "parent"], ["--caller-worktree", "caller"],
])
def test_json_seed_route_refuses_unsupported_intent_without_ssh(
    remote: Remote, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
    options: list[str],
) -> None:
    _, _, plans = remote

    def forbidden(*args: Any, **kwargs: Any) -> NoReturn:
        pytest.fail("unsupported seed intent reached SSH")

    monkeypatch.setattr(subprocess, "run", forbidden)
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
    def forbidden(*args: Any, **kwargs: Any) -> NoReturn:
        pytest.fail("unsupported prompt options reached remote admission")

    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(ValueError, match="does not support"):
        remote_seed_launch.prepare(None, "guest", "bash", ["--new", flag], "task")


@pytest.mark.parametrize("json_mode", [False, True])
def test_remote_native_resume_preserves_exact_head_and_no_resume(remote: Remote, json_mode: bool) -> None:
    _, _, plans = remote
    args = _parse([
        "--machine", "remote-host", "--worktree-id", "example-worktree",
        "--no-resume", "--native-resume-session", "native-original",
        *(["--json"] if json_mode else []),
    ])
    assert resolve_cli.cmd_resolve(args) == 0
    plan, = plans
    _, _, command = shlex.split(plan["remote_command"])
    assert "'check' '--worktree-id' 'example-worktree'" in command or (
        "check --worktree-id example-worktree" in command
    )
    forwarded = _parse(shlex.split(command.split(" && ", 1)[1])[1:])
    assert forwarded.native_resume_session == "native-original"
    assert forwarded.worktree_id == "example-worktree"
    assert forwarded.no_resume is True


def test_remote_option_inventory_covers_all_resolve_parser_destinations() -> None:
    # New options must join the forwarding/refusal policy rather than disappear.
    assert set(vars(_parse([]))) == {
        "dry_run", "recovery", "defer_projections", "no_resume", "bare_resume", "restore",
        "no_mux", "no_fast_forward", "json", "worktree_id", "codename", "base", "auto",
        "new_worktree", "bridge", "profile", "defer_new_seed", "stage_launch_seed",
        "require_native_seed", "native_resume_session", "launch_attempt_id",
        "require_launch_attempt", "require_terminal_handoff", "require_foreground_completion",
        "first_launch", "preserve_seed_id",
        "seed_id", "launch_request_b64", "launch_request_status", "machine", "environment",
        "target_no_mux", "parent_session", "caller_worktree", "owner_ref", "seed", "copilot_args",
    }


def test_powershell_remote_plan_quotes_each_value(remote: Remote) -> None:
    config, entry, plans = remote
    entry.ssh_environments[0] = replace(entry.ssh_environments[0], shell="pwsh")
    value = "profile with 'quotes'; $value"
    assert resolve_machine_cli._emit_remote_plan_for_env(
        config, entry.key, "WSL", ["--profile", value, "--no-resume", "--no-fast-forward"],
    ) == 0
    plan, = plans
    script = base64.b64decode(plan["remote_command"].rsplit(" ", 1)[1]).decode("utf-16le")
    assert "'agent-policy' 'check' '--project-root'" in script
    assert script.endswith(
        "& 'example' '--profile' 'profile with ''quotes''; $value' "
        "'--no-resume' '--no-fast-forward'; exit $LASTEXITCODE"
    )
