"""Native preparation reserves stdin for its control pump, not local tools."""

import ast
import inspect
import json
import os
import shutil
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest
from ssh_manager import CodespaceConfigSource, ConnectionManager

from agent_codespaces import auth_preflight, config, coordination, gh_account, lease, lifecycle, native_transport, worktrees


@pytest.mark.parametrize("token", ["scoped-token", None])
def test_native_account_pinning_mutates_only_github_credentials(monkeypatch, token):
    from agent_codespaces import __main__ as cli

    class Environment(dict):
        writes = []

        def __setitem__(self, key, value):
            self.writes.append(key)
            super().__setitem__(key, value)

        def update(self, *args, **kwargs):
            pytest.fail("rewriting the full environment destroys empty Windows variables")

    environment = Environment(GIT_CONFIG_COUNT="1", GIT_CONFIG_VALUE_0="", GITHUB_TOKEN="ambient")
    monkeypatch.setattr(native_transport.os, "environ", environment)
    monkeypatch.setattr(native_transport.execution_claims, "reserve", lambda *a: None)
    monkeypatch.setattr(native_transport, "InputPump", lambda: object())
    monkeypatch.setattr(native_transport.signal, "signal", lambda *a: None)
    monkeypatch.setattr(lifecycle, "account_for_codespace", lambda _: "scoped-account")
    monkeypatch.setattr(gh_account, "token_for_account", lambda account: token)
    monkeypatch.setattr(cli, "_cmd_ssh", lambda _: 0)
    args = SimpleNamespace(name="example-space", effort="owner", execution_id="execution", generation="generation")
    if token is None:
        monkeypatch.setattr(cli, "_cmd_ssh", lambda _: pytest.fail("ambient authentication was used"))
        with pytest.raises(RuntimeError, match="refusing ambient authentication"):
            native_transport.command(args)
        assert environment.writes == []
        return
    assert native_transport.command(args) == 0
    assert environment.writes == ["GH_TOKEN"]
    assert environment["GH_TOKEN"] == "scoped-token"
    assert "GITHUB_TOKEN" not in environment
    assert environment["GIT_CONFIG_VALUE_0"] == ""


@pytest.mark.skipif(sys.platform != "win32", reason="Windows CRT removes empty environment assignments")
def test_native_account_pinning_preserves_empty_git_config_in_real_child(tmp_path):
    git = shutil.which("git")
    if not git:
        pytest.skip("Git is unavailable")
    script = r"""
import json, os, subprocess, sys
from types import SimpleNamespace
from agent_codespaces import __main__ as cli, gh_account, lifecycle, native_transport as native
def query():
    value = subprocess.run([sys.argv[1], "config", "--get", "core.fsmonitor"],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
    return {"code": value.returncode, "value": value.stdout.decode().strip(),
            "missingValue": b"missing config value" in value.stderr}
if sys.argv[2] == "whole-environment":
    os.environ.update(dict(os.environ))
    result = query()
else:
    native.execution_claims.reserve = lambda *a: None
    native.InputPump = lambda: object()
    lifecycle.account_for_codespace = lambda _: "example"
    gh_account.token_for_account = lambda _: "synthetic-test-token"
    observed = []
    cli._cmd_ssh = lambda _: observed.append(query()) or 0
    args = SimpleNamespace(name="example", effort="owner", execution_id="execution", generation="generation")
    native.command(args)
    result = observed[0]
print(json.dumps(result))
"""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_CONFIG_")}
    env.pop("AGENT_CODESPACES_DISABLE_CLAIM", None)
    env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.fsmonitor", GIT_CONFIG_VALUE_0="")
    for mode, expected in (
        ("whole-environment", {"code": 128, "value": "", "missingValue": True}),
        ("native-account", {"code": 0, "value": "", "missingValue": False}),
    ):
        result = subprocess.run(
            [sys.executable, "-c", script, git, mode], env=env, cwd=tmp_path,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout.splitlines()[-1]) == expected


@pytest.mark.parametrize("inventory", [False, True])
def test_admission_calls_close_stdin_without_extending_timeout(monkeypatch, inventory):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, '{"worktrees":[]}', "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(lease, "_agent_worktrees_bin", lambda: "agent-worktrees")
    monkeypatch.setattr(coordination, "_aw", lambda: "agent-worktrees")
    if inventory:
        assert lease.active_worktree_ids() == set()
    else:
        assert coordination._run(["get", "lease-origin"]).returncode == 0
    assert len(calls) == 1
    assert calls[0][1]["stdin"] == subprocess.DEVNULL
    assert calls[0][1]["timeout"] == (15 if inventory else 45)


@pytest.mark.parametrize("function", [
    lease.active_worktree_ids,
    lease.resolve_owner_worktree,
    coordination._run,
    config.cwd_repo_root,
    config._git_origin_remote,
    config._state_root_config_dir,
    gh_account._lookup,
    gh_account._token_for_account,
    worktrees.run,
    lifecycle._list_codespaces_under,
    CodespaceConfigSource._fetch_gh_config,
    auth_preflight.enforce_host_ado_login,
])
def test_audited_preparation_boundaries_explicitly_close_stdin(function):
    tree = ast.parse(textwrap.dedent(inspect.getsource(inspect.unwrap(function))))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and (
            node.func.attr == "create_subprocess_exec"
            or node.func.attr == "run" and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
        )
    ]
    assert calls
    for call in calls:
        stdin = next((keyword.value for keyword in call.keywords if keyword.arg == "stdin"), None)
        assert stdin is not None, f"{function.__qualname__} inherits native control stdin"
        assert ast.unparse(stdin).endswith(".DEVNULL")


def test_intentional_ssh_payload_and_stdio_pipes_are_preserved():
    for function in (ConnectionManager.exec_command, ConnectionManager.open_stdio_channel):
        source = textwrap.dedent(inspect.getsource(function))
        tree = ast.parse(source)
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Attribute) and node.func.attr == "create_subprocess_exec"
                or isinstance(node.func, ast.Name) and node.func.id == "create_ssh_subprocess"
            )
        ]
        assert len(calls) == 1
        stdin = next(keyword.value for keyword in calls[0].keywords if keyword.arg == "stdin")
        assert "subprocess.PIPE" in ast.unparse(stdin)
    assert "input=input_bytes" in inspect.getsource(ConnectionManager.exec_command)
