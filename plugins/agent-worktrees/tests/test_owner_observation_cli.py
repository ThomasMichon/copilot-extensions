"""Owning parser and real bounded loopback relay, without resident recovery."""

from __future__ import annotations

import copy
import json
import socketserver
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import Any

import pytest
from test_owner_observation_history import event, record, selected

from agent_worktrees import __main__ as main
from agent_worktrees import owner_observation_cli as cli
from agent_worktrees import owner_observation_client as client
from agent_worktrees import owner_observation_contract as contract
from agent_worktrees.owner_observation_history import OwnerObservationHistory
from agent_worktrees.owner_observation_transport import FEED_ACTION, HISTORY_ACTION, READ_ACTION

JsonObject = dict[str, Any]


@dataclass
class WireState:
    answer: Callable[[JsonObject], Iterable[JsonObject | bytes]]
    requests: list[JsonObject] = field(default_factory=list)


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        incoming = contract.decode(self.rfile.readline(contract.FRAME_BYTES))
        state: WireState = self.server.state
        state.requests.append(incoming)
        assert incoming["version"] == 1 and incoming["token"] == "synthetic-token"
        for value in state.answer(incoming):
            raw = value if isinstance(value, bytes) else contract.encode(value).encode() + b"\n"
            self.wfile.write(raw)
            self.wfile.flush()


@contextmanager
def endpoint(
    answer: Callable[[JsonObject], Iterable[JsonObject | bytes]],
) -> Iterator[tuple[Any, WireState]]:
    state = WireState(answer)
    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
        server.state = state
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            yield (
                SimpleNamespace(
                    host="127.0.0.1",
                    port=server.server_address[1],
                    token="synthetic-token",
                    timeout=1.0,
                ),
                state,
            )
        finally:
            server.shutdown()
            thread.join(timeout=2)
        assert not thread.is_alive()


def health() -> JsonObject:
    return {
        "status": "ready",
        "published": True,
        "capabilities": [contract.FEED_CAPABILITY, contract.HISTORY_CAPABILITY],
        "worktrees": {"status": "ready", "generation": "a" * 32},
        "worktree_observations": {"status": "ready", "generation": "a" * 32},
    }


def request() -> JsonObject:
    return {
        "contract_version": 1,
        "project": "project",
        "worktree_id": "full-worktree",
        "expected_head_session_id": "head",
        "after_cursor": None,
    }


def args(*extra: str, history: bool = False) -> Any:
    argv = [
        "worktree-observation-history" if history else "worktree-observations",
        "--worktree",
        "full-worktree",
        "--session",
        "head",
        "--json",
        *extra,
    ]
    return main.build_parser().parse_args(argv)


@pytest.fixture
def store(tmp_path):
    value = OwnerObservationHistory(
        tmp_path / "owner.sqlite3",
        resident_generation="a" * 32,
        writer_id="b" * 32,
        authority_check=lambda: True,
    )
    value.activate()
    value.initialize(record())
    yield value
    value.close()


@pytest.mark.parametrize("history", [False, True])
def test_parser_and_lazy_dispatch_are_exact(history):
    extra = ["--source-generation", "a" * 32] if history else []
    parsed = args(*extra, history=history)
    assert parsed.func is cli.cmd_observations and parsed.contract_version == 1
    assert main._LAZY_DISPATCH_TABLE[parsed.command] == (
        "owner_observation_cli",
        "cmd_observations",
    )
    for bad in (["--unknown"], ["--jso"], ["--contract-version", "2"]):
        with pytest.raises(SystemExit):
            args(*extra, *bad, history=history)
    if history:
        with pytest.raises(SystemExit):
            args(*extra, "--follow", history=True)


@pytest.mark.parametrize("history", [False, True])
def test_real_point_transport_validates_health_and_exact_selected_reply(
    store, monkeypatch, capsys, history
):
    snapshot, _gap = store.snapshot(selected())
    supplied = record(1)
    current = store.publish(
        supplied,
        [event(supplied)],
        batch_id="batch:1",
        expected_cursor=snapshot["cursor"],
        post_save=True,
    )
    wanted = store.page(selected(), current["source_generation"], None, 1) if history else current
    action = HISTORY_ACTION if history else READ_ACTION

    def answer(incoming):
        return [{"version": 1, "result": health() if incoming["action"] == "health" else wanted}]

    with endpoint(answer) as (peer, state):
        monkeypatch.setattr(cli, "_control_client", lambda: peer)
        monkeypatch.setattr(cli.cfg, "project_name", lambda: "project")
        extra = (
            ["--source-generation", current["source_generation"], "--limit", "1"]
            if history
            else []
        )
        assert cli.cmd_observations(args(*extra, history=history)) == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == wanted and output.err == ""
    assert [item["action"] for item in state.requests] == ["health", action]
    payload = state.requests[-1]["payload"]
    assert (
        payload["worktree_id"] == "full-worktree" and payload["expected_head_session_id"] == "head"
    )
    if history:
        assert payload["before_cursor"] is None and payload["limit"] == 1
        assert "after_cursor" not in payload
    else:
        assert payload == request()


@pytest.mark.parametrize(
    "fault",
    ["unsupported", "unready", "generation", "capabilities", "draining", "unpublished", "error"],
)
def test_readiness_failure_never_sends_new_action(store, monkeypatch, capsys, fault):
    value = health()
    if fault == "unsupported":
        value["capabilities"] = []
    elif fault == "unready":
        value["worktree_observations"]["status"] = "unavailable"
    elif fault == "generation":
        value["worktree_observations"]["generation"] = "c" * 32
    elif fault == "capabilities":
        value["capabilities"] = "not-a-capability-list"
    elif fault == "draining":
        value["status"] = "draining"
    elif fault == "unpublished":
        value["published"] = False
    else:
        value["error"] = "readiness refused"
    with endpoint(lambda _request: [{"version": 1, "result": value}]) as (peer, state):
        monkeypatch.setattr(cli, "_control_client", lambda: peer)
        monkeypatch.setattr(cli.cfg, "project_name", lambda: "project")
        assert cli.cmd_observations(args()) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["type"] == "error" and output.err
    assert [item["action"] for item in state.requests] == ["health"]


@pytest.mark.parametrize("fault", ["foreign", "owner", "head", "duplicate-json", "page"])
def test_actual_wire_refuses_malformed_or_wrong_scope_responses(store, monkeypatch, capsys, fault):
    snapshot, _gap = store.snapshot(selected())
    original = client.source(snapshot)
    if fault == "foreign":
        original = replace(original, worktree_id="another-worktree")
    elif fault == "owner":
        original = replace(original, resident_generation="c" * 32)
    elif fault == "head":
        original = replace(original, head_session_id="another-head")
    body = {key: copy.deepcopy(snapshot[key]) for key in contract.BODY_FIELDS["snapshot"]}
    cursor = contract.position(original, 0)
    body["history_floor"] = body["replay_barrier"] = cursor
    frame = contract.frame("snapshot", original, cursor, body)
    if fault == "page":
        frame = store.page(selected(), snapshot["source_generation"], None)
    raw = contract.encode({"version": 1, "result": frame}).encode() + b"\n"
    if fault == "duplicate-json":
        raw = raw.replace(b'"contract_version":1', b'"contract_version":1,"contract_version":1', 1)

    def answer(incoming):
        return [{"version": 1, "result": health()}] if incoming["action"] == "health" else [raw]

    with endpoint(answer) as (peer, _state):
        monkeypatch.setattr(cli, "_control_client", lambda: peer)
        monkeypatch.setattr(cli.cfg, "project_name", lambda: "project")
        assert cli.cmd_observations(args()) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["type"] == "error"
    assert output.err and "Traceback" not in output.err


def test_real_follow_relay_delivers_frames_without_waiting_for_eof(store, monkeypatch):
    snapshot, _gap = store.snapshot(selected())
    source = client.source(snapshot)
    forwarded = threading.Event()
    frames = [
        snapshot,
        contract.frame("heartbeat", source, snapshot["cursor"], {}),
        contract.frame("going-away", source, snapshot["cursor"], {"reason": "shutdown"}),
    ]

    def answer(incoming):
        if incoming["action"] == "health":
            yield {"version": 1, "result": health()}
            return
        assert incoming["action"] == FEED_ACTION
        yield frames[0]
        assert forwarded.wait(timeout=0.5), "small frame waited for EOF instead of forwarding"
        yield from frames[1:]

    emitted = []
    with endpoint(answer) as (peer, state):
        monkeypatch.setattr(cli, "_control_client", lambda: peer)
        monkeypatch.setattr(cli.cfg, "project_name", lambda: "project")
        monkeypatch.setattr(cli, "_emit", lambda value: emitted.append(value) or forwarded.set())
        assert cli.cmd_observations(args("--follow")) == 0
    assert forwarded.is_set() and emitted == frames
    assert [item["action"] for item in state.requests] == ["health", FEED_ACTION]


@pytest.mark.parametrize("fault", [False, True])
def test_missing_project_has_structured_error_without_endpoint_or_provision(
    monkeypatch, capsys, fault
):
    monkeypatch.setattr(main, "_guard_project_scope", lambda project, command: None)

    def resolve(*_args):
        if fault:
            raise RuntimeError("synthetic project failure")
        return False

    monkeypatch.setattr(main.front_door_cli, "resolve_invocation_project", resolve)
    monkeypatch.setattr(
        cli, "_control_client", lambda: pytest.fail("unresolved project opened endpoint")
    )
    argv = [
        "worktree-observations",
        "--worktree",
        "full-worktree",
        "--json",
        "--project",
        "project",
    ]
    assert main.main(argv) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["error"]["code"] == "project-unavailable" and output.err
    with pytest.raises(SystemExit):
        main.main([*argv, "--unknown"])


def test_invalid_selection_rejected_before_health(monkeypatch, capsys):
    monkeypatch.setattr(cli.cfg, "project_name", lambda: "project")
    monkeypatch.setattr(
        cli, "_control_client", lambda: pytest.fail("invalid selector opened endpoint")
    )
    assert cli.cmd_observations(args("--session", "../wrong")) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid-request"


def test_diagnostics_escape_control_and_surrogate_text(monkeypatch, capsys):
    monkeypatch.setattr(cli.cfg, "project_name", lambda: "project")
    monkeypatch.setattr(
        cli, "_control_client", lambda: (_ for _ in ()).throw(OSError("\x00\x1b\ud800"))
    )
    assert cli.cmd_observations(args()) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["type"] == "error"
    assert "\x00" not in output.err and "\x1b" not in output.err
    assert "\\u0000" in output.err and "\\ud800" in output.err
