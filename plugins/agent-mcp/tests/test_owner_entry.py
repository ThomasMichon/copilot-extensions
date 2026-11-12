"""MCP-start negotiation and pre-effect ordinary fallback preserve caller context."""

import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from agent_mcp import custody_launch, custody_lifetime, incoming, owner_bootstrap, owner_entry
from agent_mcp.owner_capabilities import CONTRACT


class Executed(BaseException):
    pass


@pytest.fixture(autouse=True)
def linux_bootstrap(monkeypatch):
    monkeypatch.setattr(owner_bootstrap.sys, "platform", "linux")


def _request():
    return {
        "original": ["original-wrapper", "forward", "fixture"],
        "arguments": ["forward", "fixture"], "query": ["original-wrapper", "session-owner-capabilities"],
        "pid": 42, "birth": "native:1",
        "receiver": {
            "python": str(Path(sys.executable).absolute()), "prefix": str(Path(sys.prefix).resolve()),
            "module": str(Path(owner_entry.__file__).resolve()),
            "cwd": os.getcwd(),
            "environment": {key: os.environ.get(key) for key in owner_bootstrap.ENVIRONMENT_KEYS},
        },
        "selection": {"generation": "slot", "root": "unused"},
    }


def _report():
    entry = _request()["receiver"]
    # A real installed declaration keeps module code beneath its physical prefix.
    entry["module"] = str(Path(entry["prefix"]) / "lib" / "agent_mcp" / "owner_entry.py")
    return {"contract": CONTRACT, "supported": True, "invocation_allowed": True,
            "entry": entry, "selection": {"generation": "incoming"}}


def test_actual_start_probes_without_provisioning_then_execs_physical_receiver(monkeypatch):
    request = _request()
    before = os.environ.copy()
    report = _report()
    query = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(report), ""))
    physical = Mock(side_effect=Executed)
    monkeypatch.setattr(owner_bootstrap.subprocess, "run", query)
    monkeypatch.setattr(owner_bootstrap.os, "execve", physical)
    with pytest.raises(Executed):
        owner_bootstrap.start(request)
    assert query.call_args.args[0] == request["query"]
    assert query.call_args.kwargs["env"] == {**before, "AGENT_MCP_NO_SELFPROVISION": "1"}
    assert "cwd" not in query.call_args.kwargs
    command, environment = physical.call_args.args[1:]
    assert command[:5] == [
        report["entry"]["python"], "-I", "-m", "agent_mcp", "session-owner-entry",
    ]
    assert json.loads(command[5])["selection"] == {"generation": "incoming"}
    assert environment == before


@pytest.mark.parametrize("change", [
    {"supported": False}, {"invocation_allowed": False},
    {"contract": {**CONTRACT, "capabilities": ["explicit-native-owner-pair"]}},
    {"contract": {**CONTRACT, "minimum": True}},
    {"entry": {}},
])
def test_old_or_rolled_back_provider_uses_ordinary_argv_without_owner_flags(monkeypatch, change):
    request = _request()
    query = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps({
        **_report(), **change,
    }), ""))
    ordinary = Mock(side_effect=Executed)
    monkeypatch.setattr(owner_bootstrap.subprocess, "run", query)
    monkeypatch.setattr(owner_bootstrap.os, "execvpe", ordinary)
    with pytest.raises(Executed):
        owner_bootstrap.start(request)
    ordinary.assert_called_once_with("original-wrapper", request["original"], os.environ.copy())


def test_unprovisioned_query_does_not_change_eventual_ordinary_environment(monkeypatch):
    def unprovisioned(_query, **kwargs):
        assert kwargs["env"]["AGENT_MCP_NO_SELFPROVISION"] == "1"
        return subprocess.CompletedProcess([], 1, "", "runtime not provisioned")
    monkeypatch.delenv("AGENT_MCP_NO_SELFPROVISION", raising=False)
    monkeypatch.setattr(owner_bootstrap.subprocess, "run", unprovisioned)
    ordinary = Mock(side_effect=Executed)
    monkeypatch.setattr(owner_bootstrap.os, "execvpe", ordinary)
    with pytest.raises(Executed):
        owner_bootstrap.start(_request())
    assert "AGENT_MCP_NO_SELFPROVISION" not in ordinary.call_args.args[2]


@pytest.fixture
def admitted(monkeypatch, tmp_path):
    request = _request()
    # Source runs are not installed; qualify the receiver logic against this explicit fixture.
    monkeypatch.setattr(owner_entry, "receiver", lambda _report: request["receiver"])
    monkeypatch.setattr(owner_entry.sys, "platform", "linux")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_MCP_HOME", str(tmp_path))
    for key in ("AGENT_MCP_NO_MULTIPLEX", "AGENT_MCP_NO_SERVE", "AGENT_MCP_NO_ENSURE_SERVE"):
        monkeypatch.delenv(key, raising=False)
    request["receiver"]["environment"] = owner_entry.launch_environment()
    request["receiver"]["cwd"] = os.getcwd()
    layout = ("slot", tmp_path / "versions" / "slot", tmp_path)
    request["selection"] = {
        "generation": "slot", "root": str(tmp_path.resolve()),
        "prefix": str(Path(sys.prefix).resolve()), "python": str(Path(sys.executable).resolve()),
    }
    monkeypatch.setattr(incoming, "require_selected", lambda: layout)
    from agent_mcp import config, cutover, execution_context
    monkeypatch.setattr(cutover, "_require_selected", Mock())
    monkeypatch.setattr(config, "load_config", Mock())
    monkeypatch.setattr(execution_context, "_identity", Mock(return_value="qualified"))
    monkeypatch.setattr(custody_lifetime, "_pin", lambda *_args: (lambda: True, lambda: None))
    return request


@pytest.mark.parametrize("reason", ["cwd", "wrapper-cwd", "selection", "optout", "context"])
def test_receiver_ineligible_before_cold_start_preserves_ordinary_context(
    monkeypatch, admitted, tmp_path, reason,
):
    from agent_mcp import execution_context
    if reason == "cwd":
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.chdir(project)
    elif reason == "wrapper-cwd":
        admitted["receiver"]["cwd"] = str(tmp_path / "wrapper-selected-directory")
    elif reason == "selection":
        monkeypatch.setattr(incoming, "require_selected", Mock(
            side_effect=incoming.AdmissionRefused("selection changed"),
        ))
    elif reason == "context":
        monkeypatch.setattr(execution_context, "_identity", Mock(
            side_effect=execution_context.SharingUnavailable("routed context"),
        ))
    else:
        monkeypatch.setenv("AGENT_MCP_NO_SERVE", "1")
    baseline, cwd = os.environ.copy(), os.getcwd()
    ordinary, startup = Mock(side_effect=Executed), Mock()
    monkeypatch.setattr(owner_entry.os, "execvpe", ordinary)
    monkeypatch.setattr(incoming, "ensure_current", startup)
    with pytest.raises(Executed):
        owner_entry.run(admitted)
    ordinary.assert_called_once_with("original-wrapper", admitted["original"], baseline)
    assert os.environ == baseline and os.getcwd() == cwd
    startup.assert_not_called()


def test_receiver_applies_payload_wrapper_delta_before_qualifying_and_startup(monkeypatch, admitted):
    baseline = os.environ.copy()
    admitted["receiver"]["environment"]["AGENT_MCP_PAYLOAD_ROOT"] = "actual-wrapper-payload"
    monkeypatch.setenv("AGENT_MCP_PAYLOAD_ROOT", "stale-inherited-payload")
    from agent_mcp import cutover
    observed = []
    def selected(_layout):
        observed.append(os.environ["AGENT_MCP_PAYLOAD_ROOT"])
    monkeypatch.setattr(cutover, "_require_selected", selected)
    monkeypatch.setattr(
        custody_launch, "run_adopted",
        lambda *_args, **_kwargs: observed.append(os.environ["AGENT_MCP_PAYLOAD_ROOT"]),
    )
    assert owner_entry.run(admitted) == 0
    assert observed == ["actual-wrapper-payload", "actual-wrapper-payload"]
    assert os.environ["AGENT_MCP_PAYLOAD_ROOT"] == "stale-inherited-payload"
    for key, value in baseline.items():
        if key != "AGENT_MCP_PAYLOAD_ROOT":
            assert os.environ.get(key) == value


def test_qualified_receiver_keeps_strict_owner_path_and_never_replays_unknown_startup(
    monkeypatch, admitted,
):
    adopted = Mock(side_effect=incoming.OutcomeUnknown("accepted outcome unknown"))
    ordinary = Mock()
    monkeypatch.setattr(custody_launch, "run_adopted", adopted)
    monkeypatch.setattr(owner_entry.os, "execvpe", ordinary)
    assert owner_entry.run(admitted) == 1
    assert adopted.call_args.kwargs == {"owner_pid": 42, "owner_birth": "native:1"}
    ordinary.assert_not_called()


@pytest.mark.parametrize("context", ["eligible", "cwd", "selection", "optout", "sharing"])
def test_lost_originating_owner_refuses_without_startup_or_unowned_fallback(
    monkeypatch, admitted, tmp_path, context,
):
    if context == "cwd":
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.chdir(project)
    elif context == "selection":
        monkeypatch.setattr(incoming, "require_selected", Mock(
            side_effect=incoming.AdmissionRefused("selection changed"),
        ))
    elif context == "optout":
        monkeypatch.setenv("AGENT_MCP_NO_SERVE", "1")
    elif context == "sharing":
        from agent_mcp import execution_context
        monkeypatch.setattr(execution_context, "_identity", Mock(
            side_effect=execution_context.SharingUnavailable("incompatible"),
        ))
    monkeypatch.setattr(custody_lifetime, "_pin", Mock(
        side_effect=ProcessLookupError("origin exited"),
    ))
    ordinary, startup = Mock(side_effect=Executed), Mock()
    monkeypatch.setattr(owner_entry.os, "execvpe", ordinary)
    monkeypatch.setattr(incoming, "ensure_current", startup)
    with pytest.raises(owner_entry.OwnerUnavailable, match="could not be pinned"):
        owner_entry.run(admitted)
    ordinary.assert_not_called()
    startup.assert_not_called()


@pytest.mark.parametrize("eligible", [False, True])
def test_owner_death_during_preflight_cannot_execute_either_path(monkeypatch, admitted, eligible):
    alive = [True]
    monkeypatch.setattr(custody_lifetime, "_pin", lambda *_args: (
        lambda: alive[0], lambda: None,
    ))
    from agent_mcp import cutover
    def selected(_layout):
        alive[0] = False
        if not eligible:
            raise incoming.AdmissionRefused("selection moved")
    monkeypatch.setattr(cutover, "_require_selected", selected)
    ordinary, startup = Mock(side_effect=Executed), Mock()
    monkeypatch.setattr(owner_entry.os, "execvpe", ordinary)
    monkeypatch.setattr(incoming, "ensure_current", startup)
    with pytest.raises(owner_entry.OwnerUnavailable):
        owner_entry.run(admitted)
    ordinary.assert_not_called()
    startup.assert_not_called()
