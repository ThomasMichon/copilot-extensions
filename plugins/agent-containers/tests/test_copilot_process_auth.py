"""Synthetic credential and actual launch-builder contracts (no live login)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from ssh_manager import SSHConfig

from agent_containers import __main__ as cli
from agent_containers import copilot_detach as detach
from agent_containers import copilot_process_auth as auth
from agent_containers import resolver
from agent_containers.config import ContainersConfig, FleetConfig
from agent_containers.session_host_context import TrustedContext


@pytest.fixture
def launch_seams(monkeypatch):
    config = ContainersConfig(forward_gh_token=True, relay_enabled=False)
    fleet = FleetConfig(copilot_cli_account="inference-user")
    target = SimpleNamespace(config=config, fleet=fleet, user="vscode")
    seen = SimpleNamespace(staged=[], commands=[], cleaned=[], native=[])
    tokens = iter(("inference-one", "inference-two"))
    monkeypatch.setattr(resolver, "host_copilot_cli_token", lambda account: next(tokens))
    monkeypatch.setattr(cli, "host_gh_token", lambda: "git-only")
    monkeypatch.setattr(cli, "prepare_ssh_config", lambda *a: SSHConfig(host_alias="fixture"))
    monkeypatch.setattr(cli, "container_environment", lambda *a: {
        "PATH": "/usr/bin", "COPILOT_GITHUB_TOKEN": "stale-ambient",
        "GITHUB_TOKEN": "github-own", "GIT_CONFIG_COUNT": "1",
    })
    monkeypatch.setattr(cli, "cleanup_remote_envs", lambda *a: None)

    def write(name, user, values):
        seen.staged.append(dict(values))
        return f"/launch/{len(seen.staged)}.env"

    def run(argv, **kwargs):
        if "--secret-env-vars" in argv[-1]:
            return subprocess.CompletedProcess(argv, 0, "/usr/bin/copilot\n", "")
        if "cat >" in argv[-1]:
            seen.native.append(kwargs["input_text"])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(auth.transport, "_run", run)
    monkeypatch.setattr(auth.transport, "write_remote_env", write)
    monkeypatch.setattr(cli, "write_remote_env", write)
    monkeypatch.setattr(cli, "cleanup_remote_env", lambda *a: seen.cleaned.append(a[-1]))
    monkeypatch.setattr(auth.transport, "cleanup_remote_env", lambda *a: seen.cleaned.append(a[-1]))
    monkeypatch.setattr(cli, "build_ssh_command", lambda cfg, command, **kw: ["ssh", command])
    monkeypatch.setattr(auth.transport, "build_ssh_command", lambda cfg, command, **kw: ["ssh", command])
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kw:
        seen.commands.append((argv, kw)) or subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(cli, "_exec_stdio", lambda argv, env:
        seen.commands.append((argv, {"env": env})) or 0)
    return target, seen


@pytest.mark.guard
@pytest.mark.parametrize("boundary", ["acp", "session-host", "detached"])
def test_each_actual_launch_reads_updated_source_without_manager_token(
    launch_seams, monkeypatch, capsys, boundary,
):
    target, seen = launch_seams
    args = SimpleNamespace(name="example-1", stdio=False, expected_instance=None, host_relay_port=None)
    if boundary == "session-host":
        monkeypatch.setattr(cli, "_trusted_session_host_context", lambda *a:
            TrustedContext(target.config, target.fleet, target.user, "/workspace", ""))
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
            assert prepared["copilot_auth_env"]
            assert expected not in json.dumps(prepared)
        else:
            assert adapter.launch("agent-worktrees embody --detach", timeout=30)[0] == 0
        assert seen.staged[-1] == {"COPILOT_GITHUB_TOKEN": expected}
        assert "--secret-env-vars=COPILOT_GITHUB_TOKEN" in seen.native[-1]
    command_count = len(seen.commands)
    monkeypatch.setattr(resolver, "host_copilot_cli_token", lambda account: None)
    with pytest.raises(RuntimeError, match="no matching Copilot"):
        if boundary == "acp":
            cli._launch_container_agent(args, target.config, target.fleet, "trusted",
                                        target.user, "copilot --acp --stdio")
        elif boundary == "session-host":
            cli._cmd_session_host_prepare(args)
        else:
            adapter.launch("agent-worktrees embody --detach", timeout=30)
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
@pytest.mark.parametrize("command", ["/usr/bin/copilot --acp", "custom-agent --acp"])
def test_custom_acp_executable_cannot_bypass_required_process_auth(command):
    with pytest.raises(RuntimeError, match="PATH-resolved"):
        auth.wrap_command(command, "/launch/auth.env")


def test_native_launcher_consumes_token_restores_path_and_keeps_git_identity(tmp_path):
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
    assert subprocess.run([str(shell), posix(launcher)], env=environment, capture_output=True,
                          timeout=10).returncode != 0
