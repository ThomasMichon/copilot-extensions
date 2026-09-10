"""Native preparation reserves stdin for its control pump, not local tools."""

import ast
import inspect
import subprocess
import textwrap

import pytest
from ssh_manager import CodespaceConfigSource, ConnectionManager

from agent_codespaces import auth_preflight, config, coordination, gh_account, lease, lifecycle


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
    gh_account.account_for_repo,
    gh_account.token_for_account,
    gh_account.mapped_accounts,
    lifecycle._list_codespaces_under,
    CodespaceConfigSource._fetch_gh_config,
    auth_preflight.enforce_host_ado_login,
])
def test_audited_preparation_boundaries_explicitly_close_stdin(function):
    tree = ast.parse(textwrap.dedent(inspect.getsource(inspect.unwrap(function))))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"run", "create_subprocess_exec"}
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
