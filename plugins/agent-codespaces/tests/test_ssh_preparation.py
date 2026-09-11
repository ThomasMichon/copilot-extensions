"""Independent plugin delivery controls and fail-closed relay admission."""

from __future__ import annotations

import asyncio
import shlex
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_codespaces import __main__ as cli
from agent_codespaces import connection_owner as owner
from agent_codespaces import relay_readiness as readiness


@pytest.fixture
def preparation(monkeypatch, ssh_runtime):
    events, manager = ssh_runtime
    steps = {}
    for name in ("_provision_relay_helpers", "_provision_dotfiles", "_provision_harness",
                 "_provision_repo_hooks", "_verify_remote_auth", "_warm_remote_auth_cache"):
        async def record(*a, _name=name, **k):
            events.append(_name)
            return True

        steps[name] = AsyncMock(side_effect=record)
        monkeypatch.setattr(cli, name, steps[name])
    for name in ("_register_codespace_plugins", "_stage_plugins"):
        steps[name] = AsyncMock(return_value=["/workspaces/example-web/plugin"])
        monkeypatch.setattr(cli, name, steps[name])
    steps["interactive"] = AsyncMock(return_value=23)
    monkeypatch.setattr(cli, "_interactive_ssh", steps["interactive"])
    monkeypatch.setattr(cli, "_preflight_copilot_platform", AsyncMock())
    monkeypatch.setattr(cli, "_pipe_stdio", AsyncMock())
    monkeypatch.setattr(readiness, "relay_ping", lambda _: True)
    manager.exec_command = AsyncMock(return_value=SimpleNamespace(exit_code=0, timed_out=False))
    manager.open_stdio_channel = AsyncMock(return_value=SimpleNamespace(returncode=23))
    return events, manager, steps


@pytest.mark.parametrize("stdio", [False, True])
@pytest.mark.parametrize("no_staging", [False, True])
def test_plugin_staging_is_independent_of_credential_and_repo_preparation(
    preparation, stdio, no_staging,
):
    events, manager, steps = preparation
    args = ["ssh", "example-space", "--require-relay", "--stage-plugin", "example@example"]
    args += (
        ["--stdio", "--remote-cmd", "copilot --acp --stdio"] if stdio
        else ["--interactive-command", "exec bash -il"]
    )
    if no_staging:
        args += ["--no-plugin-staging"]
    assert cli.main(args) == 23
    for name in ("_provision_relay_helpers", "_provision_dotfiles", "_provision_harness",
                 "_provision_repo_hooks", "_verify_remote_auth", "_warm_remote_auth_cache"):
        steps[name].assert_awaited_once()
    for name in ("_register_codespace_plugins", "_stage_plugins"):
        assert steps[name].await_count == (0 if no_staging else 1)
    assert manager.exec_command.await_count == 2
    assert all("ping" in call.args[1] for call in manager.exec_command.call_args_list)
    assert events.index("relay-start") < events.index("_provision_dotfiles")
    assert events[-2:] == ["relay-stop", "unlock"]
    if stdio:
        steps["interactive"].assert_not_awaited()
        command = manager.open_stdio_channel.call_args.args[1]
        assert ("--plugin-dir" in command) is not no_staging
    else:
        manager.open_stdio_channel.assert_not_awaited()
        assert "--plugin-dir" not in steps["interactive"].call_args.kwargs["command"]


def test_minimal_required_relay_keeps_auth(preparation):
    _, _, steps = preparation
    assert cli.main([
        "ssh", "example-space", "--interactive-command", "true",
        "--no-provision", "--require-relay",
    ]) == 23
    for name in ("_provision_relay_helpers", "_verify_remote_auth", "_warm_remote_auth_cache"):
        steps[name].assert_awaited_once()
    for name in ("_provision_dotfiles", "_provision_harness", "_provision_repo_hooks",
                 "_register_codespace_plugins", "_stage_plugins"):
        steps[name].assert_not_awaited()


def test_required_relay_is_rechecked_after_acp_platform_preparation(monkeypatch, preparation):
    events, manager, _ = preparation

    async def platform(*a):
        events.append("platform")

    async def probe(*a, **k):
        return SimpleNamespace(exit_code=1 if "platform" in events else 0)

    monkeypatch.setattr(cli, "_preflight_copilot_platform", platform)
    manager.exec_command.side_effect = probe
    assert cli.main([
        "ssh", "example-space", "--stdio", "--remote-cmd", "copilot --acp --stdio",
        "--no-plugin-staging", "--require-relay",
    ]) == 69
    assert "platform" in events
    manager.open_stdio_channel.assert_not_awaited()
    assert events[-2:] == ["relay-stop", "unlock"]


def test_missing_host_relay_fails_before_claims(monkeypatch, preparation):
    events, manager, steps = preparation
    monkeypatch.setattr(readiness, "relay_ping", lambda _: False)
    assert cli.main(["ssh", "example-space", "--require-relay"]) == 69
    assert events == []
    manager.ensure_connected.assert_not_awaited()
    steps["interactive"].assert_not_awaited()


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("failure", ["tunnel", "remote-ping", "helpers", "post-preparation"])
def test_relay_failures_gate_only_opted_in_launches(monkeypatch, preparation, required, failure):
    events, manager, steps = preparation
    if failure == "tunnel":
        monkeypatch.setattr(cli, "_start_supervised_relay", AsyncMock(return_value=None))
    elif failure == "remote-ping":
        manager.exec_command.return_value = SimpleNamespace(exit_code=1, timed_out=False)
    elif failure == "helpers":
        steps["_provision_relay_helpers"].side_effect = None
        steps["_provision_relay_helpers"].return_value = False
    else:
        manager.exec_command.side_effect = [
            SimpleNamespace(exit_code=0, timed_out=False),
            SimpleNamespace(exit_code=1, timed_out=False),
        ]
    args = ["ssh", "example-space", "--interactive-command", "true", "--no-plugin-staging"]
    if required:
        args.append("--require-relay")
    assert cli.main(args) == (69 if required else 23)
    assert steps["interactive"].await_count == (0 if required else 1)
    assert events[-1] == "unlock"
    assert manager.disconnect.await_count >= 1
    if failure != "tunnel":
        assert "relay-stop" in events
    if required and failure in {"tunnel", "remote-ping", "helpers"}:
        steps["_provision_dotfiles"].assert_not_awaited()


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("owner_ready", [False, True])
def test_deferred_relay_readiness_and_owned_hold_cleanup(
    monkeypatch, preparation, required, owner_ready,
):
    events, _, steps = preparation
    monkeypatch.setattr(owner, "should_defer_to_owner", lambda *a, **k: True)
    monkeypatch.setattr(owner, "hold", lambda *a: events.append("hold"))
    monkeypatch.setattr(owner, "release", lambda *a: events.append("release"))
    monkeypatch.setattr(owner, "await_owner_relay", AsyncMock(return_value=owner_ready))
    monkeypatch.setattr(owner, "owner_serves_relay", lambda _: owner_ready)
    relay = AsyncMock()
    monkeypatch.setattr(cli, "_start_supervised_relay", relay)
    args = ["ssh", "example-space", "--interactive-command", "true", "--no-plugin-staging"]
    if required:
        args.append("--require-relay")
    assert cli.main(args) == (69 if required and not owner_ready else 23)
    relay.assert_not_awaited()
    assert "relay-stop" not in events
    assert events.index("lock") < events.index("hold")
    assert events[-2:] == ["release", "unlock"]
    if required and not owner_ready:
        steps["_provision_relay_helpers"].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "failure", "timeout", "exception"])
async def test_remote_probe_result_is_fail_closed(outcome):
    manager = SimpleNamespace(exec_command=AsyncMock())
    if outcome == "exception":
        manager.exec_command.side_effect = ConnectionError("unavailable")
    else:
        manager.exec_command.return_value = SimpleNamespace(
            exit_code=1 if outcome == "failure" else 0,
            timed_out=outcome == "timeout",
        )
    assert await readiness.remote_relay_ready(manager, "example-space", 9857) is (outcome == "success")
    manager.exec_command.assert_awaited_once_with(
        "example-space", readiness.remote_ping_command(9857), timeout=5.0,
    )


@pytest.mark.asyncio
async def test_real_protocol_probes_reject_non_relay_and_stalled_listeners():
    response = [b"pong\n\n"]
    requests = []

    async def serve(reader, writer):
        try:
            requests.append(await reader.readuntil(b"\n\n"))
            writer.write(response[0])
            await writer.drain()
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        assert await asyncio.to_thread(readiness.relay_ping, port)
        remote_words = shlex.split(readiness.remote_ping_command(port))
        assert remote_words[:2] == ["python3", "-c"]
        # Execute the exact remote program using the local test interpreter;
        # the real protocol server runs concurrently in the test event loop.
        proc = await asyncio.create_subprocess_exec(sys.executable, *remote_words[1:])
        assert await proc.wait() == 0
        response[0] = b"HTTP/1"
        assert not await asyncio.to_thread(readiness.relay_ping, port)
        proc = await asyncio.create_subprocess_exec(sys.executable, *remote_words[1:])
        assert await proc.wait() == 1
        response[0] = b""
        assert not await asyncio.to_thread(readiness.relay_ping, port, .05)
        assert requests == [b"ping\n\n"] * 5
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_code,timed_out", [(0, False), (1, False), (0, True)])
async def test_helper_setup_reports_status_without_changing_default_error_policy(exit_code, timed_out):
    manager = SimpleNamespace(exec_command=AsyncMock(return_value=SimpleNamespace(
        exit_code=exit_code, timed_out=timed_out, stderr="synthetic failure",
    )))
    assert await cli._provision_relay_helpers(manager, "example-space") is (exit_code == 0 and not timed_out)


@pytest.mark.asyncio
async def test_helper_setup_exception_is_reported_as_unready():
    manager = SimpleNamespace(exec_command=AsyncMock(side_effect=OSError("unavailable")))
    assert await cli._provision_relay_helpers(manager, "example-space") is False
