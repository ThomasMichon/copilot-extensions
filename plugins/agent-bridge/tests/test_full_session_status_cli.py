"""Full status preserves the actual public SessionInfo, not a compact summary."""

import argparse
import ast
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent_bridge import __main__ as core
from agent_bridge import service_start_cli
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.events import EventLog
from agent_bridge.models import SessionStatus
from agent_bridge.routes.sessions import _session_info
from agent_bridge.session_manager import Session
from agent_bridge.transport import SpawnTarget


def _args(**kwargs):
    return argparse.Namespace(session_id="bridge-a", full=True, json=True, **kwargs)


def test_full_session_status_cli_preserves_real_producer_consumer_contract(monkeypatch, capsys):
    session = Session("bridge-a", "example", SpawnTarget(
        type="local", cwd="/example/worktree", project="example", worktree_id="worktree-a",
    ))
    session.status = SessionStatus.IDLE
    session.acp_session_id = "native-a"
    session.event_log = EventLog()
    session.client = Mock(is_running=True, has_active_background_tasks=False, pid=12345)
    actual = _session_info(session).model_dump(mode="json")
    client = BridgeClient("http://example.invalid", "synthetic")
    request = Mock(return_value=actual)
    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr(core, "_get_client", lambda **_: client)
    service_start_cli._cmd_status(_args())
    output = json.loads(capsys.readouterr().out)
    assert output == actual
    request.assert_called_once_with("GET", "/api/v1/sessions/bridge-a")
    # Execute the actual consumer validator without importing a sibling runtime.
    path = (Path(__file__).resolve().parents[2] / "agent-dispatch" / "src"
            / "agent_dispatch" / "ordinary_agent_facts.py")
    validator = next(node for node in ast.parse(path.read_text()).body
                     if isinstance(node, ast.FunctionDef) and node.name == "validate_resumed")
    namespace = {"TaskError": ValueError}
    exec(compile(ast.Module(body=[validator], type_ignores=[]), str(path), "exec"), namespace)
    assert namespace["validate_resumed"](output, project="example") == actual
    with pytest.raises(ValueError):
        namespace["validate_resumed"]({"session_id": "bridge-a", "status": "idle"})


def test_full_session_status_cli_requires_exact_session(monkeypatch, capsys):
    monkeypatch.setattr(core, "_get_client", lambda **_: pytest.fail("must not read service health"))
    with pytest.raises(SystemExit) as error:
        service_start_cli._cmd_status(argparse.Namespace(session_id=None, full=True))
    assert error.value.code == 2 and "requires a session ID" in capsys.readouterr().err


def test_full_session_status_cli_flag_is_registered():
    args = core.build_parser().parse_args(["--json", "status", "bridge-a", "--full"])
    assert args.session_id == "bridge-a" and args.full is True and args.json is True


@pytest.mark.parametrize("status", [404, 409, 503])
def test_full_session_status_cli_surfaces_read_failure(monkeypatch, capsys, status):
    client = Mock()
    client.get_session.side_effect = BridgeClientError(status, "unavailable")
    monkeypatch.setattr(core, "_get_client", lambda **_: client)
    with pytest.raises(SystemExit) as error:
        service_start_cli._cmd_status(_args())
    assert error.value.code == 1
    assert capsys.readouterr().err
    client.get_session_status.assert_not_called()


def test_compact_session_status_cli_stays_unchanged(monkeypatch, capsys):
    client = Mock()
    client.get_session_status.return_value = {"session_id": "bridge-a", "status": "idle"}
    monkeypatch.setattr(core, "_get_client", lambda **_: client)
    monkeypatch.setattr(core, "_caller_id_for", lambda _: "reader")
    service_start_cli._cmd_status(argparse.Namespace(session_id="bridge-a", json=True))
    assert json.loads(capsys.readouterr().out) == client.get_session_status.return_value
    client.get_session_status.assert_called_once_with("bridge-a", caller_id="reader")
    client.get_session.assert_not_called()
