"""Synthetic credential and actual launch-builder contracts (no live login)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from ssh_manager import SSHConfig

from agent_containers import __main__ as cli
from agent_containers import copilot_detach as detach
from agent_containers import copilot_process_auth as auth
from agent_containers import copilot_venue, resolver
from agent_containers.config import ContainersConfig, FleetConfig
from agent_containers.session_host_context import TrustedContext
from agent_containers.ssh_transport import cleanup_remote_env as real_cleanup_remote_env


@pytest.fixture
def launch_seams(monkeypatch):
    def stage(_args, target, *, prepared_context, **kwargs):
        prepared_context["workspace"] = target.workspace_folder
        return "PREPARATION_GATE && "

    monkeypatch.setattr("venue_copilot.live_session_for", lambda handle: {})
    monkeypatch.setattr("agent_containers.plugin_preparation.prepare_plugins", lambda *a, **kw: [])
    monkeypatch.setattr("agent_containers.plugin_preparation.stage_for_attached_launch", stage)
    config = ContainersConfig(forward_gh_token=True, relay_enabled=False)
    fleet = FleetConfig(copilot_cli_account="inference-user")
    target = SimpleNamespace(config=config, fleet=fleet, user="vscode",
                             actual_profile="trusted", workspace_folder="/workspace")
    seen = SimpleNamespace(staged=[], commands=[], cleaned=[], native=[], created=True)
    tokens = iter(("inference-one", "inference-two"))
    monkeypatch.setattr(resolver, "host_copilot_cli_token", lambda account: next(tokens))
    monkeypatch.setattr(cli, "host_gh_token", lambda: "git-only")
    monkeypatch.setattr(resolver, "host_gh_token", lambda: "git-only")
    monkeypatch.setattr(cli, "prepare_ssh_config", lambda *a: SSHConfig(host_alias="fixture"))
    monkeypatch.setattr(cli, "container_environment", lambda *a: {
        "PATH": "/usr/bin", "COPILOT_GITHUB_TOKEN": "stale-ambient",
        "GITHUB_TOKEN": "github-own", "GIT_CONFIG_COUNT": "1",
    })
    monkeypatch.setattr(cli, "cleanup_remote_envs", lambda *a: None)
    monkeypatch.setattr(auth.transport, "container_environment", cli.container_environment)
    monkeypatch.setattr(auth.transport, "prepare_ssh_config", cli.prepare_ssh_config)

    def write(name, user, values):
        seen.staged.append(dict(values))
        return f"/launch/{len(seen.staged)}.env"

    def run(argv, **kwargs):
        if "--secret-env-vars" in argv[-1]:
            return subprocess.CompletedProcess(argv, 0, "/usr/bin/copilot\n", "")
        if "cat >" in argv[-1]:
            seen.native.append(kwargs["input_text"])
        if "COPILOT_AUTH_CONSUMED" in argv[-1]:
            return subprocess.CompletedProcess(argv, 0, "COPILOT_AUTH_CONSUMED\n", "")
        if argv[-1].endswith("/result.json"):
            payload = {"version": 1, "created": seen.created, "resumed": not seen.created}
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(auth.transport, "_run", run)
    monkeypatch.setattr(auth.transport, "write_remote_env", write)
    monkeypatch.setattr(cli, "write_remote_env", write)
    monkeypatch.setattr(cli, "cleanup_remote_env", lambda *a: seen.cleaned.append(a[-1]))
    monkeypatch.setattr(auth.transport, "cleanup_remote_env", lambda *a: seen.cleaned.append(a[-1]))
    monkeypatch.setattr(cli, "build_ssh_command", lambda cfg, command, **kw: ["ssh", command])
    monkeypatch.setattr(auth.transport, "build_ssh_command", lambda cfg, command, **kw: ["ssh", command])
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kw:
        seen.commands.append((argv, kw)) or subprocess.CompletedProcess(
            argv, 0, json.dumps({"created": seen.created, "resumed": not seen.created}), "",
        ))
    monkeypatch.setattr(cli, "_exec_stdio", lambda argv, env:
        seen.commands.append((argv, {"env": env})) or 0)
    return target, seen


@pytest.mark.guard
@pytest.mark.parametrize("boundary", ["acp", "session-host", "detached", "attached"])
def test_each_actual_launch_reads_updated_source_without_manager_token(
    launch_seams, monkeypatch, capsys, boundary,
):
    target, seen = launch_seams
    args = SimpleNamespace(name="example-1", stdio=False, expected_instance=None, host_relay_port=None)
    if boundary == "session-host":
        monkeypatch.setattr(cli, "_trusted_session_host_context", lambda *a:
            TrustedContext(target.config, target.fleet, target.user, "/workspace", ""))
    if boundary == "attached":
        import ssh_manager
        import venue_copilot

        import agent_containers.config as config_mod

        monkeypatch.setattr(config_mod, "load_config", lambda: target.config)
        monkeypatch.setattr(resolver, "resolve_live_exec_target", lambda *a, **k: target)
        monkeypatch.setattr(venue_copilot, "resolve_daemon_port", lambda: None)
        monkeypatch.setattr(ssh_manager, "TargetLock", lambda *a, **k:
            SimpleNamespace(acquire=lambda **k: None, release=lambda: None))
        monkeypatch.setattr(venue_copilot, "run_venue_copilot", lambda identity, connect, **kw:
            connect(venue_copilot.build_copilot_remote_command(identity, anchor=True)))
        args.worktree_id = None
        args.no_relay = True
        args.driver = "cli-mode"
        args.seed = None
        args.ttl_seconds = None
        args.ensure_mux = True
    adapter = detach._ContainerAdapter(
        name=args.name, ssh_config=None, hold_id="scope", remote_env="/launch/general.env",
        relay_port=None, host_relay_port=None, target=target,
    )
    for expected in ("inference-one", "inference-two"):
        if boundary == "acp":
            args.stdio = expected == "inference-two"
            assert cli._launch_container_agent(
                args, target.config, target.fleet, "trusted", target.user,
                "copilot --acp --stdio",
            ) == 0
        elif boundary == "session-host":
            assert cli._cmd_session_host_prepare(args) == 0
            prepared = json.loads(capsys.readouterr().out)
            assert ".env.d" in prepared["acp_command"]
            assert "copilot_auth_env" not in prepared
            assert expected not in json.dumps(prepared)
        elif boundary == "detached":
            assert adapter.launch("agent-worktrees embody --detach", timeout=30)[0] == 0
        else:
            assert copilot_venue.cmd_copilot(
                args, require_live_relay_port=lambda: 1, relay_healthy=lambda p: True,
                busy_exit=75, creation_flags=lambda: 0,
            ) == 0
            assert seen.commands[-1][0][-1].startswith("PREPARATION_GATE && ")
        auth_values = [
            values for values in seen.staged if list(values) == ["COPILOT_GITHUB_TOKEN"]
        ]
        assert auth_values[-1] == {"COPILOT_GITHUB_TOKEN": expected}
        assert "--secret-env-vars=COPILOT_GITHUB_TOKEN" in seen.native[-1]
    command_count = len(seen.commands)
    monkeypatch.setattr(resolver, "host_copilot_cli_token", lambda account: None)
    with pytest.raises(RuntimeError, match="no matching Copilot"):
        if boundary == "acp":
            cli._launch_container_agent(args, target.config, target.fleet, "trusted",
                                        target.user, "copilot --acp --stdio")
        elif boundary == "session-host":
            cli._cmd_session_host_prepare(args)
        elif boundary == "detached":
            adapter.launch("agent-worktrees embody --detach", timeout=30)
        else:
            copilot_venue.cmd_copilot(
                args, require_live_relay_port=lambda: 1, relay_healthy=lambda p: True,
                busy_exit=75, creation_flags=lambda: 0,
            )
    assert len(seen.commands) == command_count
    target.fleet.copilot_cli_account = None
    target.config.copilot_cli_account = None
    with pytest.raises(RuntimeError, match="requires an intended copilot_cli_account"):
        if boundary == "acp":
            cli._launch_container_agent(args, target.config, target.fleet, "trusted",
                                        target.user, "copilot --acp --stdio")
        elif boundary == "session-host":
            cli._cmd_session_host_prepare(args)
        elif boundary == "detached":
            adapter.launch("agent-worktrees embody --detach", timeout=30)
        else:
            copilot_venue.cmd_copilot(
                args, require_live_relay_port=lambda: 1, relay_healthy=lambda p: True,
                busy_exit=75, creation_flags=lambda: 0,
            )
    assert len(seen.commands) == command_count
    for environment in seen.staged:
        if "GH_TOKEN" in environment:
            assert environment["GH_TOKEN"] == "git-only"
            assert environment["GITHUB_TOKEN"] == "github-own"
            assert "COPILOT_GITHUB_TOKEN" not in environment
    for argv, kwargs in seen.commands:
        assert "inference-one" not in str(argv) and "inference-two" not in str(argv)
        assert "COPILOT_GITHUB_TOKEN" not in kwargs.get("env", {})
        assert ".env.d" in argv[-1]


@pytest.mark.guard
def test_explicit_source_failure_blocks_before_probe_or_staging(monkeypatch):
    monkeypatch.setattr(resolver, "host_copilot_cli_token", lambda account: None)
    monkeypatch.setattr(auth.transport, "_run", lambda *a, **k: pytest.fail("no probe"))
    with pytest.raises(RuntimeError, match="no matching Copilot"):
        auth.prepare_launch(ContainersConfig(copilot_cli_account="inference-user"),
                            FleetConfig(), "example-1", "vscode")


@pytest.mark.guard
def test_unsupported_native_cli_is_not_weakened(monkeypatch):
    monkeypatch.setattr(resolver, "host_copilot_cli_token", lambda account: "synthetic")
    monkeypatch.setattr(auth.transport, "_run", lambda argv, **kw:
        subprocess.CompletedProcess(argv, 1, "", ""))
    monkeypatch.setattr(auth.transport, "write_remote_env", lambda *a: pytest.fail("no staging"))
    with pytest.raises(RuntimeError, match="--secret-env-vars"):
        auth.prepare_launch(ContainersConfig(copilot_cli_account="inference-user"),
                            FleetConfig(), "example-1", "vscode")


@pytest.mark.guard
@pytest.mark.skipif(sys.platform != "linux", reason="Linux POSIX ACL extended attributes")
def test_native_launcher_directory_is_private_with_inherited_default_acl(tmp_path, monkeypatch):
    import errno
    import struct

    directory = tmp_path / "launch"
    directory.mkdir(mode=0o700)
    acl = struct.pack("<I", 2) + b"".join(
        struct.pack("<HHI", tag, permissions, 0xFFFFFFFF)
        for tag, permissions in ((1, 7), (4, 5), (32, 6))
    )
    try:
        os.setxattr(directory, "system.posix_acl_default", acl)
    except OSError as error:
        if error.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
            pytest.skip("Filesystem does not support POSIX default ACLs")
        raise
    auth_env = str(directory / "input.env")
    monkeypatch.setattr(resolver, "host_copilot_cli_token", lambda account: "synthetic")
    monkeypatch.setattr(auth.transport, "write_remote_env", lambda *a: auth_env)
    real_guard = auth.directory_guard
    # No credential is staged here; retain all guards except the unrelated tmpfs check.
    monkeypatch.setattr(
        auth, "directory_guard",
        lambda path, **kwargs: real_guard(path, **{"require_ram": False, **kwargs}),
    )

    def execute(argv, **kwargs):
        if "--secret-env-vars" in argv[-1]:
            return subprocess.CompletedProcess(argv, 0, "/usr/bin/copilot\n", "")
        return subprocess.run(
            ["sh", "-c", argv[-1]], input=kwargs.get("input_text"),
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    monkeypatch.setattr(auth.transport, "_run", execute)
    assert auth.prepare_launch(
        ContainersConfig(copilot_cli_account="inference-user"), None, "example-1", "vscode",
    ) == auth_env
    assert (Path(auth.launcher_directory(auth_env)).stat().st_mode & 0o777) == 0o700


@pytest.mark.guard
@pytest.mark.parametrize("command", [
    "/usr/bin/copilot --acp", "custom-agent --acp",
    "echo copilot && custom-agent --acp", "custom-agent copilot",
    "copilot --acp && custom-agent",
    'cd "$(custom-agent)" && copilot --acp',
    "{{target_preference_launcher}}",
    "{{target_preference_launcher}} --acp --stdio",
    "{{target_preference_launcher}} custom-agent --acp",
    "{{target_preference_launcher}} /usr/bin/copilot --acp",
])
def test_custom_acp_executable_cannot_bypass_required_process_auth(command):
    with pytest.raises(RuntimeError, match="PATH-resolved"):
        auth.wrap_command(command, "/launch/auth.env")


@pytest.mark.guard
@pytest.mark.parametrize("command", [
    "copilot --acp --stdio",
    "exec copilot --acp --stdio",
    "cd '/workspace with spaces' && exec copilot --acp --stdio",
    "cd /workspace && {{target_preference_launcher}} copilot --acp --stdio",
])
def test_supported_executable_positions_remain_available(command):
    assert auth.wrap_command(command, "/launch/auth.env").endswith(command)


@pytest.mark.guard
def test_detached_success_waits_for_consumption_before_cleanup(launch_seams, monkeypatch):
    target, seen = launch_seams
    adapter = detach._ContainerAdapter(
        name="example-1", ssh_config=None, hold_id="scope", remote_env=None,
        relay_port=None, host_relay_port=None, target=target,
    )
    consumption = []

    def consumed(name, user, path, **kwargs):
        assert path not in seen.cleaned
        consumption.append(path)

    monkeypatch.setattr(auth, "wait_for_consumption", consumed)
    assert adapter.launch("agent-worktrees embody --detach", timeout=30)[0] == 0
    assert consumption and consumption[0] in seen.cleaned


@pytest.mark.guard
@pytest.mark.parametrize("outcome", ["nonzero", "timeout", "invalid-receipt", "unconsumed"])
def test_detached_ambiguous_launch_retains_inputs(launch_seams, monkeypatch, capsys, outcome):
    target, seen = launch_seams
    adapter = detach._ContainerAdapter(
        name="example-1", ssh_config=None, hold_id="scope", remote_env="/launch/general.env",
        relay_port=None, host_relay_port=None, target=target,
    )

    def run(argv, **kwargs):
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        if outcome == "invalid-receipt":
            return subprocess.CompletedProcess(argv, 0, "interrupted-response", "")
        if outcome == "unconsumed":
            return subprocess.CompletedProcess(argv, 0, '{"created":true,"resumed":false}', "")
        return subprocess.CompletedProcess(argv, 255, "", "connection lost")

    monkeypatch.setattr(detach.subprocess, "run", run)
    def unconsumed(*args, **kwargs):
        raise RuntimeError("unconsumed launch")

    monkeypatch.setattr(auth, "wait_for_consumption", unconsumed)
    if outcome == "nonzero":
        assert adapter.launch("agent-worktrees embody --detach", timeout=1)[0] == 255
    else:
        with pytest.raises((RuntimeError, subprocess.TimeoutExpired)):
            adapter.launch("agent-worktrees embody --detach", timeout=1)
    assert not seen.cleaned
    assert adapter.inputs_safe_to_cleanup is False
    assert "bounded abandoned-input cleanup" in capsys.readouterr().err


@pytest.mark.guard
def test_detached_nonzero_resume_receipt_allows_unused_input_cleanup(launch_seams, monkeypatch):
    target, seen = launch_seams
    adapter = detach._ContainerAdapter(
        name="example-1", ssh_config=None, hold_id="scope", remote_env=None,
        relay_port=None, host_relay_port=None, target=target,
    )
    monkeypatch.setattr(detach.subprocess, "run", lambda argv, **kwargs:
        subprocess.CompletedProcess(argv, 255, '{"created":false,"resumed":true}', "attach failed"))
    monkeypatch.setattr(auth, "wait_for_consumption", lambda *a, **k: pytest.fail("no new child"))
    assert adapter.launch("agent-worktrees embody --detach", timeout=1)[0] == 255
    assert adapter.inputs_safe_to_cleanup is True
    assert seen.cleaned


@pytest.mark.guard
def test_detached_prelaunch_failure_cleans_unused_inputs(launch_seams, monkeypatch):
    target, seen = launch_seams
    adapter = detach._ContainerAdapter(
        name="example-1", ssh_config=None, hold_id="scope", remote_env=None,
        relay_port=None, host_relay_port=None, target=target,
    )

    def reject_ssh(*args, **kwargs):
        raise RuntimeError("invalid SSH binding")

    monkeypatch.setattr(auth.transport, "build_ssh_command", reject_ssh)
    with pytest.raises(RuntimeError, match="invalid SSH binding"):
        adapter.launch("agent-worktrees embody --detach", timeout=1)
    assert adapter.inputs_safe_to_cleanup is True
    assert seen.cleaned


@pytest.mark.guard
def test_missing_consumption_receipt_refuses_success(monkeypatch):
    monkeypatch.setattr(auth.transport, "_run", lambda argv, **kwargs:
        subprocess.CompletedProcess(argv, 0, "", ""))
    with pytest.raises(RuntimeError, match="did not confirm consuming"):
        auth.wait_for_consumption("example-1", "vscode", "/launch/auth.env", timeout=1)


@pytest.mark.guard
def test_detached_rejoin_does_not_wait_or_claim_refresh(launch_seams, monkeypatch):
    target, seen = launch_seams
    seen.created = False
    monkeypatch.setattr(auth, "wait_for_consumption", lambda *a, **k: pytest.fail("not a new child"))
    adapter = detach._ContainerAdapter(
        name="example-1", ssh_config=None, hold_id="scope", remote_env=None,
        relay_port=None, host_relay_port=None, target=target,
    )
    assert adapter.launch("agent-worktrees embody --detach", timeout=30)[0] == 0
    assert seen.cleaned


@pytest.mark.guard
def test_attached_rejoin_receipt_is_not_consumption(launch_seams, monkeypatch):
    target, seen = launch_seams
    seen.created = False
    assert auth.attached_created_child("example-1", "vscode", "/launch/auth.env") is False
    assert auth.created_child({"created": False, "resumed": True}) is False
    with pytest.raises(RuntimeError, match="no explicit create-or-resume"):
        auth.created_child({"created": False, "resumed": False})


@pytest.mark.guard
def test_original_session_host_cleanup_handle_removes_companion_auth(launch_seams, monkeypatch):
    target, _ = launch_seams
    owner = "/home/user/.agent-containers/launch/" + "a" * 32 + ".env"
    commands: list[list[str]] = []
    monkeypatch.setattr(auth.transport, "_run", lambda argv, **kw:
        commands.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(cli, "cleanup_remote_env", real_cleanup_remote_env)
    monkeypatch.setattr(cli, "_trusted_session_host_context", lambda *a:
        TrustedContext(target.config, target.fleet, "vscode", "/workspace", ""))
    assert cli.main(["session-host-cleanup", "example-1", "--remote-env", owner]) == 0
    removal = next(command for command in commands if "rm" in command)
    assert owner + ".d/auth.env" in removal
    assert owner + ".d/copilot" in removal


@pytest.mark.guard
def test_empty_general_environment_still_has_a_backwards_compatible_owner():
    staged = []
    owner = auth.stage_env("example-1", "vscode", {},
                           lambda name, user, values: staged.append(values) or "/launch/owner.env",
                           require_owner=True)
    assert owner == "/launch/owner.env"
    assert staged == [{"AGENT_CONTAINERS_LAUNCH_INPUTS": "1"}]


@pytest.mark.guard
def test_wrapper_transport_failure_cleans_staged_token_and_owner(launch_seams, monkeypatch):
    target, seen = launch_seams

    def run(argv, **kwargs):
        if "--secret-env-vars" in argv[-1]:
            return subprocess.CompletedProcess(argv, 0, "/usr/bin/copilot\n", "")
        if "refuse_launch_storage" in argv[-1] and "cat >" not in argv[-1]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise RuntimeError("synthetic wrapper transport failure")

    monkeypatch.setattr(auth.transport, "_run", run)
    with pytest.raises(RuntimeError, match="synthetic wrapper transport failure"):
        auth.prepare_launch(target.config, target.fleet, "example-1", "vscode",
                            cleanup_owner="/launch/owner.env")
    assert "/launch/1.env" in seen.cleaned and "/launch/owner.env" in seen.cleaned


@pytest.mark.parametrize("folder", ["ordinary", "spaces and [glob]"])
def test_native_launcher_consumes_token_restores_path_and_keeps_git_identity(tmp_path, folder):
    tmp_path = tmp_path / folder
    tmp_path.mkdir()
    if os.name == "nt":
        git = shutil.which("git")
        shell = Path(git).parents[1] / "bin" / "bash.exe" if git else None
        if shell is None or not shell.is_file():
            pytest.skip("Git Bash required to exercise the POSIX target launcher on Windows")
    else:
        shell = shutil.which("sh")
        if shell is None:
            pytest.skip("POSIX shell required")

    def posix(path):
        value = str(path).replace("\\", "/")
        return "/" + value[0].lower() + value[2:] if os.name == "nt" else value

    native = tmp_path / "native"
    native.write_text(
        "#!/bin/sh\nset -eu\n"
        'test "$1" = "--secret-env-vars=COPILOT_GITHUB_TOKEN"\n'
        'test "$COPILOT_GITHUB_TOKEN" = "synthetic-inference"\n'
        'test "$GH_TOKEN" = "synthetic-git"\n'
        'test "$GITHUB_TOKEN" = "synthetic-github"\n'
        'case "$PATH" in *.env.d*) exit 2;; esac\n'
        # Emulate the documented native shell/MCP environment contract. The
        # option/value assertions above are the native launch acceptance gate.
        "unset COPILOT_GITHUB_TOKEN\n"
        "sh -c 'test -z \"${COPILOT_GITHUB_TOKEN:-}\" && "
        "test \"$GH_TOKEN\" = synthetic-git'\n"
    )
    native.chmod(0o700)
    auth_env = posix(tmp_path / "auth.env")
    directory = tmp_path / "auth.env.d"
    directory.mkdir()
    launcher = directory / "copilot"
    launcher.write_text(auth.launcher_script(posix(native), auth_env))
    launcher.chmod(0o700)
    (tmp_path / "auth.env").write_text("export COPILOT_GITHUB_TOKEN=synthetic-inference\n")
    environment = dict(os.environ, GH_TOKEN="synthetic-git", GITHUB_TOKEN="synthetic-github")
    command = auth.wrap_command("exec copilot --acp --stdio", auth_env)
    result = subprocess.run([str(shell), "-c", command], env=environment,
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "auth.env").exists()
    assert (directory / "consumed").read_text().strip() == "1"
    assert subprocess.run([str(shell), posix(launcher)], env=environment, capture_output=True,
                          timeout=10).returncode != 0
