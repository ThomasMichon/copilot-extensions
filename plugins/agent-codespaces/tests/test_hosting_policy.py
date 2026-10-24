"""Repository admission is a guest read, not the host venue-name policy."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent_codespaces import hosting_policy


@pytest.mark.asyncio
async def test_invalid_explicit_installation_context_precedes_guest_transport(monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", " ")
    execute = AsyncMock(side_effect=AssertionError("invalid context must not reach a guest"))
    monkeypatch.setattr("agent_codespaces._ssh_retry.exec_with_retry", execute)
    with pytest.raises(RuntimeError, match="installation context refused"):
        await hosting_policy.require_guest_hosting(object(), "venue", "/workspaces/product")
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_denial_uses_actual_guest_checkout_without_fallback(monkeypatch):
    execute = AsyncMock(return_value=SimpleNamespace(
        exit_code=1, stdout="", stderr="expose_agent: false",
    ))
    monkeypatch.setattr("agent_codespaces._ssh_retry.exec_with_retry", execute)
    with pytest.raises(RuntimeError, match="expose_agent: false"):
        await hosting_policy.require_guest_hosting(object(), "venue", "/workspaces/product")
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
