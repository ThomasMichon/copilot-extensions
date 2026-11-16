"""Exact, account-aware availability reads without launching SSH."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from ssh_manager import CodespaceConfigSource, SSHConfig

from agent_bridge.session_host import codespace_transport
from agent_bridge.db import Database

NAME = "example-target"
pytestmark = pytest.mark.asyncio


def _result(
    data: object = None, *, stdout: str | None = None, stderr: str = "", code: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        [], code, json.dumps(data) if stdout is None else stdout, stderr,
    )


def _denied(status: int = 404) -> subprocess.CompletedProcess[str]:
    return _result(stdout="", stderr=f"gh: Request denied (HTTP {status})", code=1)


@pytest.fixture
def run_gh(monkeypatch: pytest.MonkeyPatch) -> Mock:
    monkeypatch.setenv("GH_TOKEN", "ambient-token")
    monkeypatch.setenv("GITHUB_TOKEN", "other-ambient-token")
    run = Mock()
    monkeypatch.setattr(subprocess, "run", run)
    return run


@pytest.mark.parametrize("state", ["Available", "Shutdown", "Unavailable", "Starting"])
async def test_exact_target_state_has_no_list_limit(run_gh: Mock, state: str) -> None:
    run_gh.return_value = _result({"name": NAME, "state": state})

    assert await codespace_transport.CodeSpaceTransport(NAME).is_running() is (
        state == "Available"
    )

    run_gh.assert_called_once()
    call = run_gh.call_args
    assert call.args[0] == [
        "gh", "api", f"/user/codespaces/{NAME}",
        "--method", "GET", "--hostname", "github.com",
        "--jq", "{name: .name, state: .state}",
    ]
    assert 0 < call.kwargs["timeout"] <= 30
    assert call.kwargs["creationflags"] == codespace_transport._creation_flags()


@pytest.mark.parametrize("status", [401, 403, 404])
@pytest.mark.parametrize("login_label", ["account", "as"])
async def test_wrong_ambient_account_retries_explicit_keyring_accounts(
    run_gh: Mock, status: int, login_label: str,
) -> None:
    status_text = (
        f"  Logged in to github.com {login_label} wrong-account (keyring)\n"
        f"  Logged in to github.com {login_label} owning-account (keyring)\n"
        f"  Logged in to github.com {login_label} owning-account (keyring)\n"
    )
    run_gh.side_effect = [
        _denied(status),
        _result(stdout="", stderr=status_text),
        _result(stdout="wrong-token\n"),
        _denied(),
        _result(stdout="owner-token\n"),
        _result({"name": NAME, "state": "Available"}),
    ]

    assert await codespace_transport.CodeSpaceTransport(NAME).is_running() is True

    calls = run_gh.call_args_list
    assert len(calls) == 6
    assert calls[0].kwargs["env"]["GH_TOKEN"] == "ambient-token"
    assert calls[0].kwargs["env"]["GITHUB_TOKEN"] == "other-ambient-token"
    assert calls[1].args[0] == ["gh", "auth", "status", "--hostname", "github.com"]
    for call in (calls[1], calls[2], calls[4]):
        assert "GH_TOKEN" not in call.kwargs["env"]
        assert "GITHUB_TOKEN" not in call.kwargs["env"]
    assert calls[4].args[0] == [
        "gh", "auth", "token", "--hostname", "github.com", "--user", "owning-account",
    ]
    assert calls[5].args[0][1:3] == ["api", f"/user/codespaces/{NAME}"]
    assert calls[5].kwargs["env"]["GH_TOKEN"] == "owner-token"
    assert "GITHUB_TOKEN" not in calls[5].kwargs["env"]
    assert all("ssh" not in c.args[0] and "list" not in c.args[0] for c in calls)


async def test_verified_fallback_account_reaches_ssh_config_and_proxy(
    run_gh: Mock, tmp_path: Path,
) -> None:
    source = CodespaceConfigSource(NAME, config_dir=tmp_path)
    transport = codespace_transport.CodeSpaceTransport(NAME, source=source)
    run_gh.side_effect = [
        _denied(),
        _result(stdout="Logged in to github.com account owning-account\n"),
        _result(stdout="owner-token"),
        _result({"name": NAME, "state": "Available"}),
        _result(stdout=(
            "Host example-target\n"
            "  User codespace\n"
            "  ProxyCommand gh codespace ssh --stdio -c example-target\n"
        )),
    ]

    assert await transport.is_running() is True
    config = source.get_ssh_config()

    assert run_gh.call_args.args[0] == ["gh", "codespace", "ssh", "--config", "-c", NAME]
    assert run_gh.call_args.kwargs["env"]["GH_TOKEN"] == "owner-token"
    assert config.process_env is not None
    assert config.process_env["GH_TOKEN"] == "owner-token"
    assert "GITHUB_TOKEN" not in config.process_env
    assert config.config_file is not None
    assert Path(config.config_file).parent == tmp_path


async def test_first_connection_resolves_account_without_prior_availability_read(
    run_gh: Mock,
) -> None:
    from ssh_manager import ConnectionManager

    manager = Mock(spec=ConnectionManager)
    manager.ensure_connected = AsyncMock()
    manager.exec_command = AsyncMock(
        return_value=SimpleNamespace(exit_code=0, stdout="ok", stderr=""),
    )
    transport = codespace_transport.CodeSpaceTransport(NAME, manager=manager)
    run_gh.side_effect = [
        _denied(),
        _result(stdout="Logged in to github.com account owning-account\n"),
        _result(stdout="owner-token"),
        _result({"name": NAME, "state": "Available"}),
    ]

    assert await transport.run("echo ok") == (0, "ok", "")
    await transport.run("echo ok")

    manager.ensure_connected.assert_awaited_once()
    bound_source = manager.ensure_connected.await_args.args[1]
    assert bound_source.gh_env["GH_TOKEN"] == "owner-token"
    assert len(run_gh.call_args_list) == 4


async def test_remote_reap_restores_account_without_persisting_credentials(
    run_gh: Mock, tmp_db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge.session_host.endpoints import endpoint_from_ssh_config
    from agent_bridge.session_manager import SessionManager

    endpoint = json.loads(json.dumps(endpoint_from_ssh_config(
        SSHConfig(host_alias=NAME, process_env={"GH_TOKEN": "original-token"}),
        50000, 50001, kind="codespace", extra={"codespace": NAME},
    )))
    assert "original-token" not in json.dumps(endpoint)
    record = SimpleNamespace(session_id="retained-session", host_pid=300, child_pid=301)
    bound_configs: list[SSHConfig] = []

    async def connect(
        host: str, source: CodespaceConfigSource, _forwards: list[Any],
    ) -> None:
        assert host == NAME
        source._config_dir = tmp_path
        bound_configs.append(source.get_ssh_config())

    manager = SimpleNamespace(
        ensure_connected=AsyncMock(side_effect=connect),
        exec_command=AsyncMock(return_value=SimpleNamespace(
            exit_code=0, stdout="__REAPED__", stderr="",
        )),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr("ssh_manager.ConnectionManager", lambda: manager)
    run_gh.side_effect = [
        _denied(403),
        _result(stdout="Logged in to github.com account owning-account\n"),
        _result(stdout="renewed-owner-token"),
        _result({"name": NAME, "state": "Available"}),
        _result(stdout=(
            f"Host {NAME}\n"
            "  User codespace\n"
            f"  ProxyCommand gh codespace ssh --stdio -c {NAME}\n"
        )),
    ]

    assert await SessionManager(tmp_db)._remote_reap(record, endpoint)

    assert bound_configs[0].process_env is not None
    assert bound_configs[0].process_env["GH_TOKEN"] == "renewed-owner-token"
    assert run_gh.call_args.kwargs["env"]["GH_TOKEN"] == "renewed-owner-token"
    manager.exec_command.assert_awaited_once()
    command = manager.exec_command.await_args.args[1]
    assert "kill -TERM -300" in command
    assert "alive 300 || alive 301" in command
    manager.disconnect.assert_awaited_once_with(NAME)
    assert "renewed-owner-token" not in json.dumps(endpoint)


async def test_remote_reap_cannot_release_custody_when_account_lookup_fails(
    run_gh: Mock, tmp_db: Database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge.session_manager import SessionManager

    manager = SimpleNamespace(
        ensure_connected=AsyncMock(), exec_command=AsyncMock(), disconnect=AsyncMock(),
    )
    monkeypatch.setattr("ssh_manager.ConnectionManager", lambda: manager)
    run_gh.side_effect = [_denied(403), _result(stdout="no accounts")]
    owner = SessionManager(tmp_db)
    release = Mock()
    monkeypatch.setattr(owner, "_release_unprotected_codespace_lock", release)
    record = SimpleNamespace(session_id="retained-session", host_pid=300, child_pid=301)

    assert not await owner._remote_reap(record, {"kind": "codespace", "codespace": NAME})

    manager.ensure_connected.assert_not_awaited()
    manager.exec_command.assert_not_awaited()
    release.assert_not_called()


@pytest.mark.parametrize("name", [None, "", " "])
async def test_remote_reap_rejects_malformed_persisted_codespace_name(
    run_gh: Mock, tmp_db: Database, monkeypatch: pytest.MonkeyPatch, name: str | None,
) -> None:
    from agent_bridge.session_manager import SessionManager

    manager = SimpleNamespace(
        ensure_connected=AsyncMock(), exec_command=AsyncMock(), disconnect=AsyncMock(),
    )
    monkeypatch.setattr("ssh_manager.ConnectionManager", lambda: manager)
    record = SimpleNamespace(session_id="retained-session", host_pid=300, child_pid=301)

    assert not await SessionManager(tmp_db)._remote_reap(
        record, {"kind": "codespace", "codespace": name},
    )

    run_gh.assert_not_called()
    manager.ensure_connected.assert_not_awaited()
    manager.exec_command.assert_not_awaited()


@pytest.mark.parametrize("accessible", [True, False])
async def test_explicit_source_account_is_not_replaced_by_ambient_auth(
    run_gh: Mock, accessible: bool,
) -> None:
    source_env = {"GH_TOKEN": "pinned-token"}
    source = CodespaceConfigSource(NAME, gh_env=source_env)
    transport = codespace_transport.CodeSpaceTransport(NAME, source=source)
    run_gh.return_value = (
        _result({"name": NAME, "state": "Available"}) if accessible else _denied()
    )
    if accessible:
        assert await transport.is_running() is True
    else:
        with pytest.raises(RuntimeError, match="configured account"):
            await transport.is_running()
    run_gh.assert_called_once()
    assert run_gh.call_args.kwargs["env"] == source_env
    assert source_env == {"GH_TOKEN": "pinned-token"}


@pytest.mark.parametrize("failure", ["missing-token", "empty-token", "denied"])
async def test_no_verified_account_is_unknown_not_unavailable(
    run_gh: Mock, failure: str,
) -> None:
    responses = [
        _denied(),
        _result(stdout="Logged in to github.com account example-account (keyring)\n"),
    ]
    if failure == "missing-token":
        responses.append(_result(stdout="", stderr="keyring unavailable", code=1))
    elif failure == "empty-token":
        responses.append(_result(stdout=""))
    else:
        responses.extend([_result(stdout="scoped-token"), _denied()])
    run_gh.side_effect = responses

    with pytest.raises(RuntimeError, match="No authenticated GitHub account"):
        await codespace_transport.CodeSpaceTransport(NAME).is_running()

    assert len(run_gh.call_args_list) == len(responses)
    assert all(
        "switch" not in call.args[0] and "login" not in call.args[0]
        for call in run_gh.call_args_list
    )


@pytest.mark.parametrize("payload", [
    [],
    {"name": "different-target", "state": "Available"},
    {"name": NAME, "state": None},
    {"name": NAME, "state": ""},
])
async def test_malformed_or_mismatched_target_cannot_be_treated_as_stopped(
    run_gh: Mock, payload: object,
) -> None:
    run_gh.return_value = _result(payload)
    with pytest.raises(RuntimeError, match="requested target"):
        await codespace_transport.CodeSpaceTransport(NAME).is_running()
    run_gh.assert_called_once()


async def test_invalid_json_is_an_error(run_gh: Mock) -> None:
    run_gh.return_value = _result(stdout="{invalid")
    with pytest.raises(RuntimeError, match="invalid JSON"):
        await codespace_transport.CodeSpaceTransport(NAME).is_running()


@pytest.mark.parametrize("error", [OSError("missing gh"), subprocess.TimeoutExpired("gh", 30)])
async def test_process_failures_remain_inconclusive(
    run_gh: Mock, error: Exception,
) -> None:
    run_gh.side_effect = error
    with pytest.raises(RuntimeError, match="lookup failed"):
        await codespace_transport.CodeSpaceTransport(NAME).is_running()
    run_gh.assert_called_once()


async def test_server_failure_does_not_fan_out_to_other_accounts(run_gh: Mock) -> None:
    run_gh.return_value = _result(stdout="", stderr="gh: server error (HTTP 500)", code=1)
    with pytest.raises(RuntimeError, match="could not verify"):
        await codespace_transport.CodeSpaceTransport(NAME).is_running()
    run_gh.assert_called_once()


async def test_account_queries_share_one_deadline(
    run_gh: Mock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        codespace_transport, "time",
        SimpleNamespace(monotonic=Mock(side_effect=[100.0, 105.0, 110.0, 131.0])),
    )
    run_gh.side_effect = [
        _denied(),
        _result(stdout="Logged in to github.com account example-account\n"),
    ]
    with pytest.raises(RuntimeError, match="lookup timed out"):
        await codespace_transport.CodeSpaceTransport(NAME).is_running()
    assert [call.kwargs["timeout"] for call in run_gh.call_args_list] == [25.0, 20.0]


async def test_unknown_account_output_is_not_an_unavailable_target(run_gh: Mock) -> None:
    run_gh.side_effect = [_denied(), _result(stdout="unrecognized status output")]
    with pytest.raises(RuntimeError, match="enumerate GitHub accounts"):
        await codespace_transport.CodeSpaceTransport(NAME).is_running()


async def test_nonzero_auth_status_can_still_supply_another_valid_account(run_gh: Mock) -> None:
    run_gh.side_effect = [
        _denied(),
        _result(stdout="", stderr="Logged in to github.com as valid-account\n", code=1),
        _result(stdout="valid-token"),
        _result({"name": NAME, "state": "Available"}),
    ]
    assert await codespace_transport.CodeSpaceTransport(NAME).is_running() is True


async def test_target_name_is_encoded_as_one_api_path_component(run_gh: Mock) -> None:
    name = "example/target?state=Available"
    run_gh.return_value = _result({"name": name, "state": "Available"})
    assert await codespace_transport.CodeSpaceTransport(name).is_running() is True
    assert run_gh.call_args.args[0][2] == (
        "/user/codespaces/example%2Ftarget%3Fstate%3DAvailable"
    )


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("Available", True),
        ("Shutdown", False),
        ("Archived", False),
        ("Starting", None),
        ("ShuttingDown", None),
        ("Provisioning", None),
        ("Rebuilding", None),
    ],
)
async def test_relay_reconnect_gate_retires_only_on_stopped_states(
    run_gh: Mock, state: str, expected: bool | None,
) -> None:
    run_gh.return_value = _result({"name": NAME, "state": state})
    transport = codespace_transport.CodeSpaceTransport(NAME)

    if expected is None:
        # Transitional: inconclusive, so the relay backs off and re-checks.
        with pytest.raises(RuntimeError, match=state):
            await transport.reconnect_allowed()
    else:
        assert await transport.reconnect_allowed() is expected
