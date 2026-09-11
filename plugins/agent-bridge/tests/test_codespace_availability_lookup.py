"""Exact, account-aware availability reads without launching SSH."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_bridge.session_host import codespace_transport

NAME = "example-target"
pytestmark = pytest.mark.asyncio


def _result(data=None, *, stdout=None, stderr="", code=0):
    return subprocess.CompletedProcess(
        [], code, json.dumps(data) if stdout is None else stdout, stderr,
    )


def _denied(status=404):
    return _result(stdout="", stderr=f"gh: Request denied (HTTP {status})", code=1)


@pytest.fixture
def run_gh(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ambient-token")
    monkeypatch.setenv("GITHUB_TOKEN", "other-ambient-token")
    run = Mock()
    monkeypatch.setattr(codespace_transport.subprocess, "run", run)
    return run


@pytest.mark.parametrize("state", ["Available", "Shutdown", "Unavailable", "Starting"])
async def test_exact_target_state_has_no_list_limit(run_gh, state):
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
    run_gh, status, login_label,
):
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


@pytest.mark.parametrize("accessible", [True, False])
async def test_explicit_source_account_is_not_replaced_by_ambient_auth(run_gh, accessible):
    source_env = {"GH_TOKEN": "pinned-token"}
    source = SimpleNamespace(_gh_env=source_env)
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
async def test_no_verified_account_is_unknown_not_unavailable(run_gh, failure):
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
async def test_malformed_or_mismatched_target_cannot_be_treated_as_stopped(run_gh, payload):
    run_gh.return_value = _result(payload)
    with pytest.raises(RuntimeError, match="requested target"):
        await codespace_transport.CodeSpaceTransport(NAME).is_running()
    run_gh.assert_called_once()


async def test_invalid_json_is_an_error(run_gh):
    run_gh.return_value = _result(stdout="{invalid")
    with pytest.raises(RuntimeError, match="invalid JSON"):
        await codespace_transport.CodeSpaceTransport(NAME).is_running()


@pytest.mark.parametrize("error", [OSError("missing gh"), subprocess.TimeoutExpired("gh", 30)])
async def test_process_failures_remain_inconclusive(run_gh, error):
    run_gh.side_effect = error
    with pytest.raises(RuntimeError, match="lookup failed"):
        await codespace_transport.CodeSpaceTransport(NAME).is_running()
    run_gh.assert_called_once()


async def test_server_failure_does_not_fan_out_to_other_accounts(run_gh):
    run_gh.return_value = _result(stdout="", stderr="gh: server error (HTTP 500)", code=1)
    with pytest.raises(RuntimeError, match="could not verify"):
        await codespace_transport.CodeSpaceTransport(NAME).is_running()
    run_gh.assert_called_once()


async def test_account_queries_share_one_deadline(run_gh, monkeypatch):
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


async def test_unknown_account_output_is_not_an_unavailable_target(run_gh):
    run_gh.side_effect = [_denied(), _result(stdout="unrecognized status output")]
    with pytest.raises(RuntimeError, match="enumerate GitHub accounts"):
        await codespace_transport.CodeSpaceTransport(NAME).is_running()


async def test_nonzero_auth_status_can_still_supply_another_valid_account(run_gh):
    run_gh.side_effect = [
        _denied(),
        _result(stdout="", stderr="Logged in to github.com as valid-account\n", code=1),
        _result(stdout="valid-token"),
        _result({"name": NAME, "state": "Available"}),
    ]
    assert await codespace_transport.CodeSpaceTransport(NAME).is_running() is True


async def test_target_name_is_encoded_as_one_api_path_component(run_gh):
    name = "example/target?state=Available"
    run_gh.return_value = _result({"name": name, "state": "Available"})
    assert await codespace_transport.CodeSpaceTransport(name).is_running() is True
    assert run_gh.call_args.args[0][2] == (
        "/user/codespaces/example%2Ftarget%3Fstate%3DAvailable"
    )
