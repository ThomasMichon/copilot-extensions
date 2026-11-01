"""Actual owning parser and bounded CLI relay, no autoensure or planning imports."""

from __future__ import annotations

import copy
import io
import json
import socketserver
import threading
from types import SimpleNamespace

import pytest

from agent_worktrees import __main__ as main
from agent_worktrees import agenda_cli as cli
from agent_worktrees import agenda_contract as contract
from agent_worktrees.status_monitor_cutover import ControlClient


def request():
    return {
        "contract_version": 1,
        "project": "project",
        "worktree_id": "full-id",
        "session_id": "head",
    }


def reply(sequence=0):
    return {
        "ok": True,
        "contract_version": 1,
        "generation": "a" * 32,
        "sequence": sequence,
        "project": "project",
        "worktree_id": "full-id",
        "session_id": "head",
        "availability": "not-published",
        "observation": None,
    }


@pytest.mark.parametrize("name", ["agenda-publish", "agenda-read", "agenda-feed"])
def test_main_parser_and_lazy_dispatch_are_owned_and_strict(name):
    argv = [name, "--json"]
    if name != "agenda-publish":
        argv += ["--worktree", "full-id", "--session", "head"]
    args = main.build_parser().parse_args(argv)
    assert args.func is cli.cmd_agenda
    assert main._LAZY_DISPATCH_TABLE[name] == ("agenda_cli", "cmd_agenda")
    with pytest.raises(SystemExit):
        main.build_parser().parse_args([*argv, "--unknown"])
    with pytest.raises(SystemExit):
        main.build_parser().parse_args([*argv, "--jso"])


def test_actual_main_global_project_selection(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "cmd_agenda", lambda args: calls.append(args) or 0)
    monkeypatch.setattr(main, "_guard_project_scope", lambda project, command: None)
    monkeypatch.setattr(
        main.front_door_cli,
        "resolve_invocation_project",
        lambda args, project, *rest: calls.append(project) or True,
    )
    assert (
        main.main(
            [
                "agenda-feed",
                "--project",
                "project",
                "--worktree",
                "full-id",
                "--session",
                "head",
                "--json",
            ]
        )
        == 0
    )
    assert calls[-1].command == "agenda-feed"
    assert calls[0] == "project"


def test_read_uses_existing_authenticated_client_and_forwards_exact_reply(monkeypatch, capsys):
    calls = []

    class Client:
        def _request(self, action, payload=None, **bounds):
            calls.append((action, payload, bounds))
            if action == "health":
                return {
                    "capabilities": [contract.CAPABILITY],
                    "worktrees": {"status": "ready", "generation": "a" * 32},
                }
            return reply()

    monkeypatch.setattr(cli, "_control_client", Client)
    monkeypatch.setattr(cli.cfg, "project_name", lambda: "project")
    args = SimpleNamespace(command="agenda-read", worktree="full-id", session="head", json=True)
    assert cli.cmd_agenda(args) == 0
    assert json.loads(capsys.readouterr().out) == reply()
    assert calls[1][0:2] == ("agenda-read", request())
    assert calls[1][2]["total_timeout"] == 1.0


@pytest.mark.parametrize(
    "health,code",
    [
        ({"capabilities": []}, "unsupported"),
        (
            {"capabilities": [contract.CAPABILITY], "worktrees": {"status": "draining"}},
            "unavailable",
        ),
        (
            {
                "capabilities": [contract.CAPABILITY],
                "worktrees": {"status": "ready", "generation": "bad"},
            },
            "invalid-request",
        ),
    ],
)
def test_missing_support_not_success_shaped_or_provisioned(monkeypatch, capsys, health, code):
    calls = []
    client = SimpleNamespace(_request=lambda *args, **kwargs: calls.append(args) or health)
    monkeypatch.setattr(cli, "_control_client", lambda: client)
    monkeypatch.setattr(cli.cfg, "project_name", lambda: "project")
    args = SimpleNamespace(command="agenda-read", worktree="full-id", session="head", json=True)
    assert cli.cmd_agenda(args) == 1
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["ok"] is False and result["error"]["code"] == code
    assert output.err and len(calls) == 1


@pytest.mark.parametrize("raw", ['{"schema":1,"schema":2}', '{"x":NaN}', " " * 32769])
def test_publisher_stdin_is_strict_and_bounded_before_health(monkeypatch, capsys, raw):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(raw))
    monkeypatch.setattr(
        cli, "_control_client", lambda: pytest.fail("invalid stdin reached endpoint")
    )
    assert cli.cmd_agenda(SimpleNamespace(command="agenda-publish", json=True)) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_receiver_exact_frames_identity_generation_cursor_and_terminal():
    receiver = cli.Receiver(request(), "a" * 32)
    first = {"type": "snapshot", **reply()}
    receiver.receive(first)
    receiver.receive({"type": "heartbeat", "generation": "a" * 32, "sequence": 0})
    for change in (
        {"type": "snapshot"},
        {"generation": "b" * 32},
        {"project": "foreign"},
        {"worktree_id": "suffix"},
        {"sequence": True},
        {"extra": None},
    ):
        bad = {"type": "update", **reply(1), **change}
        with pytest.raises(contract.AgendaError):
            receiver.receive(bad)
    advanced = {"type": "update", **reply(1), "availability": "head-changed"}
    receiver.receive(advanced)
    receiver.receive(
        {"type": "going-away", "generation": "a" * 32, "sequence": 1, "reason": "draining"}
    )
    with pytest.raises(contract.AgendaError):
        receiver.receive(copy.deepcopy(first))


def test_receiver_requires_snapshot_and_structured_errors():
    receiver = cli.Receiver(request(), "a" * 32)
    with pytest.raises(contract.AgendaError):
        receiver.receive({"type": "update", **reply(1)})
    receiver.receive({"type": "error", **contract.failure("unavailable", "Synthetic fault")})
    assert receiver.closed


@pytest.mark.parametrize("kind", [[], {}, None, False, 17])
def test_receiver_rejects_non_text_type_before_membership(kind):
    receiver = cli.Receiver(request(), "a" * 32)
    with pytest.raises(contract.AgendaError) as error:
        receiver.receive({"type": kind})
    assert error.value.code == "invalid-response"
    assert receiver.previous is None


@pytest.mark.parametrize("field", ["availability", "generation", "sequence", "session_id"])
@pytest.mark.parametrize("value", [[], {}])
def test_receiver_malformed_fields_raise_domain_errors_not_type_errors(field, value):
    receiver = cli.Receiver(request(), "a" * 32)
    with pytest.raises(contract.AgendaError):
        receiver.receive({"type": "snapshot", **reply(), field: value})
    assert receiver.previous is None


@pytest.mark.parametrize("kind", [[], {}])
def test_actual_relay_non_text_type_returns_structured_error_without_traceback(
    monkeypatch,
    capsys,
    kind,
):
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            incoming = json.loads(self.rfile.readline(contract.FRAME_BYTES))
            if incoming["action"] == "health":
                result = {
                    "version": 1,
                    "result": {
                        "capabilities": [contract.CAPABILITY],
                        "worktrees": {"status": "ready", "generation": "a" * 32},
                    },
                }
            else:
                assert incoming["action"] == "agenda-feed"
                result = {"type": kind}
            self.wfile.write(json.dumps(result).encode() + b"\n")
            self.wfile.flush()

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        client = ControlClient(
            f"http://127.0.0.1:{server.server_address[1]}",
            token="synthetic-token",
        )
        monkeypatch.setattr(cli, "_control_client", lambda: client)
        monkeypatch.setattr(cli.cfg, "project_name", lambda: "project")
        try:
            assert (
                cli.cmd_agenda(
                    SimpleNamespace(
                        command="agenda-feed",
                        worktree="full-id",
                        session="head",
                        json=True,
                    )
                )
                == 1
            )
            output = capsys.readouterr()
            frame = json.loads(output.out)
            assert frame["type"] == "error" and frame["ok"] is False
            assert frame["error"]["code"] == "invalid-response"
            assert output.err and "Traceback" not in output.err
        finally:
            server.shutdown()
            thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.parametrize("fault", [False, True])
def test_actual_main_project_failure_is_structured_and_strict(monkeypatch, capsys, fault):
    monkeypatch.setattr(main, "_guard_project_scope", lambda project, command: None)

    def resolve(*args):
        if fault:
            raise RuntimeError("synthetic project resolution failure")
        return False

    monkeypatch.setattr(main.front_door_cli, "resolve_invocation_project", resolve)
    argv = [
        "agenda-read",
        "--project",
        "project",
        "--worktree",
        "full-id",
        "--session",
        "head",
        "--json",
    ]
    assert main.main(argv) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["error"]["code"] == "project-unavailable"
    assert output.err
    with pytest.raises(SystemExit):
        main.main([*argv, "--unknown"])
