"""Repository admission is a guest read, not the host venue-name policy."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent_codespaces import hosting_policy


@pytest.mark.asyncio
@pytest.mark.parametrize("preflight", [False, True])
async def test_invalid_explicit_installation_context_precedes_guest_transport(monkeypatch, preflight):
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", " ")
    execute = AsyncMock(side_effect=AssertionError("invalid context must not reach a guest"))
    monkeypatch.setattr("agent_codespaces._ssh_retry.exec_with_retry", execute)
    with pytest.raises(RuntimeError, match="installation context refused"):
        await hosting_policy.require_guest_hosting(
            object(), "venue", "/workspaces/product", preflight=preflight,
        )
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_denial_uses_actual_guest_checkout_without_fallback(monkeypatch):
    execute = AsyncMock(return_value=SimpleNamespace(
        exit_code=1, stdout="", stderr="expose_agent: false",
    ))
    monkeypatch.setattr("agent_codespaces._ssh_retry.exec_with_retry", execute)
    with pytest.raises(RuntimeError, match="expose_agent: false"):
        await hosting_policy.require_guest_hosting(
            object(), "venue", "/workspaces/product", preflight=True,
        )
    assert execute.await_count == 1
    command = execute.call_args.args[2]
    assert "/workspaces/product" in command
    assert "agent-policy check" in command
    assert "--path" in command


@pytest.mark.asyncio
async def test_unsupported_receiver_is_visible(monkeypatch):
    execute = AsyncMock(return_value=SimpleNamespace(
        exit_code=2, stdout="", stderr="unrecognized command",
    ))
    monkeypatch.setattr("agent_codespaces._ssh_retry.exec_with_retry", execute)
    with pytest.raises(RuntimeError, match="receiver unsupported"):
        await hosting_policy.require_guest_hosting(object(), "venue", "/workspaces/harness")
    assert execute.await_count == 1


@pytest.mark.asyncio
async def test_absent_receiver_uses_fresh_canonical_reader_before_any_provisioning(monkeypatch):
    import json

    execute = AsyncMock(side_effect=[
        SimpleNamespace(exit_code=127, stdout="__HOSTING_RECEIVER_ABSENT__\n", stderr=""),
        SimpleNamespace(exit_code=0, stderr="", stdout=json.dumps({
            "version": 1, "allowed": True, "repository": "/workspaces/product",
            "expose_agent": None, "sources": [], "denied_by": [],
        })),
    ])
    monkeypatch.setattr("agent_codespaces._ssh_retry.exec_with_retry", execute)
    await hosting_policy.require_guest_hosting(
        object(), "venue", "/workspaces/product", preflight=True,
    )
    assert execute.await_count == 2
    probe = execute.call_args_list[1]
    assert "python3 -I -S -c" in probe.args[2]
    assert "/workspaces/product" in probe.args[2]
    assert probe.kwargs["input_bytes"].startswith(b"PK")
    assert "plugin install" not in probe.args[2] and "/scripts/install" not in probe.args[2]


@pytest.mark.asyncio
async def test_fresh_reader_denial_remains_a_terminal_preflight_refusal(monkeypatch):
    execute = AsyncMock(side_effect=[
        SimpleNamespace(exit_code=127, stdout="__HOSTING_RECEIVER_ABSENT__\n", stderr=""),
        SimpleNamespace(exit_code=3, stdout='{"error":"expose_agent: false"}', stderr=""),
    ])
    monkeypatch.setattr("agent_codespaces._ssh_retry.exec_with_retry", execute)
    with pytest.raises(RuntimeError, match="expose_agent: false"):
        await hosting_policy.require_guest_hosting(
            object(), "venue", "/workspaces/product", preflight=True,
        )
    assert execute.await_count == 2


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [False, True])
async def test_fresh_policy_reader_gates_actual_preparation_boundary(monkeypatch, tmp_path, allowed):
    import os
    import shlex
    import subprocess
    import sys

    from agent_codespaces.plugin_preparation import prepare_plugins

    policy = tmp_path / ".agent-worktrees" / "config.yaml"
    policy.parent.mkdir()
    policy.write_text("expose_agent: " + ("true" if allowed else "false") + "\n")
    monkeypatch.setattr(
        "agent_codespaces.plugin_preparation.plugin_plan",
        lambda *args, **kwargs: {"plugins": [], "marketplaces": {}},
    )
    commands = []

    async def execute(_manager, _name, command, **kwargs):
        commands.append(command)
        if len(commands) == 1:
            return SimpleNamespace(exit_code=127, stdout="__HOSTING_RECEIVER_ABSENT__\n", stderr="")
        if len(commands) == 2:
            words = shlex.split(command)
            assert words[:2] == ["bash", "-lc"]
            words = shlex.split(words[2])
            assert words[:4] == ["python3", "-I", "-S", "-c"]
            environment = dict(os.environ)
            environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
            result = subprocess.run(
                [sys.executable, *words[1:5], str(tmp_path)],
                input=kwargs["input_bytes"], capture_output=True, timeout=10, env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            return SimpleNamespace(
                exit_code=result.returncode, stdout=result.stdout.decode(), stderr=result.stderr.decode(),
            )
        assert allowed and command == "bash -ls", "policy refusal reached plugin preparation"
        return SimpleNamespace(exit_code=0, stdout="__VENUE_PLUGINS__{}", stderr="")

    monkeypatch.setattr("agent_codespaces._ssh_retry.exec_with_retry", execute)
    if allowed:
        assert await prepare_plugins(
            object(), "synthetic", None, object(), "/workspaces/product",
        ) == []
        assert len(commands) == 3
    else:
        with pytest.raises(RuntimeError, match="expose_agent: false"):
            await prepare_plugins(object(), "synthetic", None, object(), "/workspaces/product")
        assert len(commands) == 2


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
@pytest.mark.asyncio
async def test_login_context_refuses_absent_receiver_before_preparation(monkeypatch, tmp_path):
    import os
    from pathlib import Path
    import shlex
    import shutil
    import subprocess
    import sys

    from agent_codespaces.plugin_preparation import prepare_plugins

    if os.name == "nt":
        bash = Path(os.environ["ProgramFiles"]) / "Git" / "bin" / "bash.exe"
        if not bash.is_file():
            pytest.skip("requires Git Bash, not a WSL launcher")
    else:
        bash = shutil.which("bash")
        if bash is None:
            pytest.skip("requires bash")
    bash = str(bash).replace("\\", "/")
    python = sys.executable.replace("\\", "/")
    workspace = tmp_path.as_posix()
    if os.name == "nt":
        workspace = "/" + workspace[0].lower() + workspace[2:]
    (tmp_path / ".git").mkdir()
    profile = tmp_path / "guest-login.sh"
    # Supply login-only initialization without loading real host profiles.
    profile.write_text(
        'if shopt -q login_shell; then\n'
        '  export COPILOT_EXTENSIONS_CONTEXT=fixture-explicit-context\n'
        'fi\n'
        f'bash() {{ {shlex.quote(bash)} --noprofile --norc "$@"; }}\n'
        f'python3() {{ {shlex.quote(python)} "$@"; }}\n'
        'command() {\n'
        '  if [ "$1" = -v ] && [ "$2" = agent-worktrees ]; then return 1; fi\n'
        '  builtin command "$@"\n'
        '}\n',
        encoding="utf-8",
    )
    environment = dict(os.environ, BASH_ENV=profile.as_posix())
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    monkeypatch.setattr(
        "agent_codespaces.plugin_preparation.plugin_plan",
        lambda *args, **kwargs: {"plugins": [], "marketplaces": {}},
    )
    results = []

    async def execute(_manager, _name, command, **kwargs):
        assert len(results) < 2, "explicit guest context reached plugin preparation"
        result = subprocess.run(
            [bash, "--noprofile", "--norc", "-c", command],
            input=kwargs.get("input_bytes"), capture_output=True, timeout=15, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        results.append(result)
        return SimpleNamespace(
            exit_code=result.returncode, stdout=result.stdout.decode(), stderr=result.stderr.decode(),
        )

    monkeypatch.setattr("agent_codespaces._ssh_retry.exec_with_retry", execute)
    with pytest.raises(RuntimeError, match="cannot validate an explicit installation context"):
        await prepare_plugins(object(), "synthetic", None, object(), workspace)
    assert len(results) == 2
    assert results[0].returncode == 127
    assert results[0].stdout.strip() == b"__HOSTING_RECEIVER_ABSENT__"
    assert results[1].returncode == 3


def test_launch_guard_precedes_native_effects_and_keeps_caller_flags():
    command = hosting_policy.guarded_launch("copilot --acp", "/workspaces/execution harness")
    assert command.index("agent-policy check") < command.index("copilot --acp")
    assert 'copilot --acp "$@"' in command
    assert "|| return $?" in command


@pytest.mark.parametrize("workspace", ["", ".", "/workspaces/invalid\0"])
def test_missing_actual_checkout_is_not_a_host_path_fallback(workspace):
    with pytest.raises(RuntimeError, match="absolute guest checkout"):
        hosting_policy.guest_check_command(workspace)


@pytest.mark.parametrize("skip_plugins", [False, True])
@pytest.mark.parametrize("reason", ["expose_agent: false", "invalid policy", "receiver unsupported"])
def test_direct_stdio_refuses_before_claim_token_or_provisioning(
    monkeypatch, skip_plugins, reason,
):
    from agent_codespaces import __main__ as cli
    from agent_codespaces.config import CodespacesConfig

    monkeypatch.setattr(cli.launch_policy, "stdio_refused_exit_code", lambda _args: None)
    monkeypatch.setattr(cli, "_lookup_codespace_repo", lambda _name: "example/product")
    monkeypatch.setattr(
        "agent_codespaces.config.load_merged_config",
        lambda: CodespacesConfig(workspace_folder="/workspaces/product"),
    )
    admit = Mock(side_effect=RuntimeError(reason))
    claim = Mock(side_effect=AssertionError("denied hosting reached a claim"))
    token = Mock(side_effect=AssertionError("denied hosting minted a credential"))
    provision = AsyncMock(side_effect=AssertionError("denied hosting reached provisioning"))
    monkeypatch.setattr(hosting_policy, "require_codespace_hosting", admit)
    monkeypatch.setattr("agent_codespaces.lease.claim_for_connect", claim)
    monkeypatch.setattr("agent_codespaces.relay_launch.scoped_relay_token", token)
    monkeypatch.setattr(cli, "_provision_repo_hooks", provision)
    with pytest.raises(RuntimeError, match=reason):
        cli._cmd_ssh(SimpleNamespace(
            name="venue", stdio=True, remote_cmd="copilot --acp",
            skip_plugin_preparation=skip_plugins,
        ))
    admit.assert_called_once_with("venue", "/workspaces/product", worktree_id=None)
    claim.assert_not_called()
    token.assert_not_called()
    provision.assert_not_awaited()


def test_stdio_admission_preserves_exact_prepared_worktree(monkeypatch):
    from agent_codespaces import __main__ as cli
    from agent_codespaces.config import CodespacesConfig

    monkeypatch.setattr(cli, "_lookup_codespace_repo", lambda _name: "example/product")
    monkeypatch.setattr(
        "agent_codespaces.config.load_merged_config",
        lambda: CodespacesConfig(workspace_folder="/workspaces/anchor"),
    )
    admit = Mock()
    monkeypatch.setattr(hosting_policy, "require_codespace_hosting", admit)
    hosting_policy.admit_stdio_launch(SimpleNamespace(
        name="venue", stdio=True, plugin_worktree_id="exact",
        plugin_prepared_context={"workspace": "/workspaces/actual"},
    ))
    admit.assert_called_once_with("venue", "/workspaces/actual", worktree_id="exact")


def test_diagnostic_source_ssh_does_not_require_agent_hosting(monkeypatch):
    from agent_codespaces import __main__ as cli

    monkeypatch.setattr(
        cli, "_lookup_codespace_repo",
        Mock(side_effect=AssertionError("diagnostic source SSH queried agent hosting")),
    )
    admit = Mock(side_effect=AssertionError("diagnostic source SSH required an agent"))
    monkeypatch.setattr(hosting_policy, "require_codespace_hosting", admit)
    hosting_policy.admit_stdio_launch(SimpleNamespace(stdio=False))
    admit.assert_not_called()


@pytest.mark.parametrize("state", ["Shutdown", "Starting", None])
def test_read_only_admission_never_wakes_an_uninspectable_codespace(monkeypatch, state):
    from agent_codespaces.lifecycle import CodespaceInfo

    monkeypatch.setattr(
        "agent_codespaces.lifecycle.list_codespaces",
        lambda: [CodespaceInfo("raw-venue", "friendly", "example/product", "main", state, "standard")],
    )
    connection = Mock(side_effect=AssertionError("policy inspection attempted to wake a venue"))
    monkeypatch.setattr("ssh_manager.ConnectionManager", connection)
    with pytest.raises(RuntimeError, match="without waking"):
        hosting_policy.require_codespace_hosting("friendly", "/workspaces/product")
    connection.assert_not_called()


def test_missing_codespace_refuses_before_connection_or_account_projection(monkeypatch):
    monkeypatch.setattr("agent_codespaces.lifecycle.list_codespaces", lambda: [])
    connection = Mock(side_effect=AssertionError("unknown venue reached a connection"))
    monkeypatch.setattr("ssh_manager.ConnectionManager", connection)
    with pytest.raises(RuntimeError, match="read-only hosting admission"):
        hosting_policy.require_codespace_hosting("missing", "/workspaces/product")
    connection.assert_not_called()


@pytest.mark.parametrize("live_attachment", [False, True])
def test_available_codespace_admission_uses_the_live_account_and_raw_identity(
    monkeypatch, live_attachment,
):
    import json

    from agent_codespaces.lifecycle import CodespaceInfo

    monkeypatch.setattr(
        "agent_codespaces.lifecycle.list_codespaces",
        lambda: [CodespaceInfo(
            "raw-venue", "friendly", "example/product", "main", "Available", "standard",
            account="fixture-account",
        )],
    )
    source = Mock()
    monkeypatch.setattr("agent_codespaces.codespace_config.CodespaceSource", source)
    manager = SimpleNamespace(
        ensure_connected=AsyncMock(), disconnect=AsyncMock(),
        exec_command=AsyncMock(return_value=SimpleNamespace(
            exit_code=0, timed_out=False, stderr="", stdout=json.dumps({
                "version": 1, "allowed": True, "repository": "/workspaces/product",
                "expose_agent": None, "sources": [], "denied_by": [],
            }),
        )),
    )
    monkeypatch.setattr("ssh_manager.ConnectionManager", lambda: manager)
    hosting_policy.require_codespace_hosting(
        "friendly", "/workspaces/product", live_attachment=live_attachment,
    )
    source.assert_called_once_with("raw-venue", account="fixture-account")
    manager.ensure_connected.assert_awaited_once_with("raw-venue", source.return_value, [])
    manager.disconnect.assert_awaited_once_with("raw-venue")
