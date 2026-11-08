"""Pinned local owner, strict CLI and independently negotiated client controls."""

import json
from subprocess import CompletedProcess
from unittest.mock import MagicMock

import pytest

from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.mux_question_backend import LocalMux
from agent_bridge.mux_question_forms import MuxQuestionError


def test_backend_uses_selected_owner_and_exact_confirmed_native_pane(monkeypatch):
    from agent_bridge import mux_question_backend as backend

    calls = []
    binding = {
        "found": True, "session_id": "native", "worktree_id": "worktree",
        "mux_session": "wt-worktree", "pane_id": "%7", "pane_pid": 123,
        "pane_start_time": "1", "copilot_pid": 456, "copilot_start_time": "2",
    }
    pane_rows = [
        "wt-worktree|@2|0|%8|999|$4:1234",
        "wt-worktree|@3|1|%7|123|$4:1234",
    ]
    def owner(**kwargs):
        assert kwargs == {"allow_path_fallback": False}
        return ["selected-python", "-m", "agent_worktrees"]
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        assert kwargs["stdin"] is not None and kwargs["timeout"] == 10
        if argv[0] == "selected-python":
            assert argv[-4:] == ["session-binding", "--session-id", "native", "--json"]
            return CompletedProcess(argv, 0, json.dumps(binding), "")
        if argv[1] == "list-panes":
            assert argv[2] == "-s" and argv[argv.index("-t") + 1] == "wt-worktree"
            return CompletedProcess(argv, 0, "\n".join(pane_rows), "")
        assert argv[argv.index("-t") + 1] == "wt-worktree:@3.1"
        return CompletedProcess(argv, 0, "pane", "")
    monkeypatch.setattr(backend, "_agent_worktrees_launch_prefix", owner)
    monkeypatch.setattr(backend.shutil, "which", lambda _: "selected-mux")
    monkeypatch.setattr(backend.subprocess, "run", run)
    mux = LocalMux()
    observed = mux.binding("native", {"worktree_id": "worktree"})
    assert observed["incarnation"] == "$4:1234"
    assert mux.capture(observed) == "pane"
    mux.key(observed, "-literal ; answer", literal=True)
    assert calls[-1][0] == [
        "selected-mux", "send-keys", "-t", "wt-worktree:@3.1", "-l", "--", "-literal ; answer",
    ]
    valid = pane_rows[1]
    pane_rows.append(valid)
    with pytest.raises(MuxQuestionError):
        mux.binding("native", {"worktree_id": "worktree"})
    pane_rows.pop()
    for changed in (
        valid.replace("|123|", "|999|"), valid.replace("@3", "unknown"),
        valid.replace("|1|", "|unknown|"), valid.replace("$4:1234", "unknown"),
    ):
        pane_rows[1] = changed
        with pytest.raises(MuxQuestionError):
            mux.binding("native", {"worktree_id": "worktree"})
    pane_rows[1] = valid
    binding["copilot_start_time"] = ""
    with pytest.raises(MuxQuestionError):
        mux.binding("native", {"worktree_id": "worktree"})


def test_old_provider_never_receives_mux_mutation_and_writes_are_not_retried():
    client = BridgeClient("http://127.0.0.1:1", "synthetic-secret")
    sent = []
    capable = False
    def request(method, path, **kwargs):
        if path == "/health":
            return {"protocol_version": 52, "min_protocol_version": 1,
                    "capabilities": {"mux_question_answers": 1} if capable else {}}
        sent.append((method, path, kwargs))
        return {"state": "answered"}
    client._request = request
    with pytest.raises(BridgeClientError):
        client.answer_mux_question("native", "inspection", sender="controller", option=1)
    assert sent == []
    capable = True
    client.answer_mux_question("native", "inspection", sender="controller", text="literal")
    assert sent[-1][2]["allow_retry"] is False
    assert sent[-1][2]["body"] == {
        "inspection_id": "inspection", "sender": "controller", "option": None, "text": "literal",
    }
    client.mux_question_answer("native", "inspection")
    assert sent[-1][:2] == ("GET", "/api/v1/live-sessions/native/questions/mux/answers/inspection")


def test_cli_mux_does_not_start_a_daemon_or_touch_native_or_permission_paths(monkeypatch, capsys):
    from agent_bridge import __main__ as core
    from agent_bridge.session_lifecycle_cli import _cmd_answer, _cmd_questions

    client = MagicMock()
    client.answer_mux_question.return_value = {"state": "answered"}
    def selected(**kwargs):
        assert kwargs == {"ensure": False}
        return client
    monkeypatch.setattr(core, "_get_client", selected)
    monkeypatch.setattr(core, "_live_sender_label", lambda _: "controller")
    parser = core.build_parser()
    args = parser.parse_args(["answer", "native", "--via", "mux", "--inspection-id", "inspection", "--option", "1"])
    _cmd_answer(args)
    assert json.loads(capsys.readouterr().out)["state"] == "answered"
    client.answer_permission.assert_not_called()
    client.answer_live_question.assert_not_called()
    client.answer_ask_user.assert_not_called()
    client.get_session_status.assert_not_called()
    _cmd_questions(parser.parse_args(["questions", "native", "--via", "mux", "--inspection-id", "inspection"]))
    client.mux_question_answer.assert_called_once_with("native", "inspection")
    for more in (["--option-id", "approve"], ["--generation", "native"], ["--cancel"], ["--field", "x=y"]):
        with pytest.raises(SystemExit) as exc:
            _cmd_answer(parser.parse_args([
                "answer", "native", "--via", "mux", "--inspection-id", "inspection", "--text", "answer", *more,
            ]))
        assert exc.value.code == 2
    with pytest.raises(SystemExit):
        _cmd_answer(parser.parse_args(["answer", "native", "--text", "answer"]))
