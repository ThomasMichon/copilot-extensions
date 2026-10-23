"""The actual authenticated roster transport never computes current facts."""

from __future__ import annotations

import json
import socketserver
import threading
import time

import pytest

from agent_worktrees import status_monitor_cutover as control
from agent_worktrees import worktrees_projection as projection
from agent_worktrees import worktrees_runtime as runtime


def request(**selection):
    return {"contract_version": runtime.CONTRACT_VERSION, "project": "project", **selection}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        request(contract_version=True),
        request(contract_version=3),
        request(project="../project"),
        request(include_missing=1),
        request(worktree_id=""),
        request(history=True),
    ],
)
def test_worktrees_runtime_rejects_malformed_requests_without_storage(tmp_path, payload):
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3")
    result = owner.handle(payload)
    assert result["ok"] is False and result["error"]["code"] == "invalid-request"
    assert not owner.store.db_path.exists()


def test_worktrees_runtime_cold_unready_and_deleted_roster_are_distinct(tmp_path):
    wake = threading.Event()
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3", wake=wake)
    assert owner.handle(request())["error"]["code"] == "unready"
    owner.activate()
    assert owner.handle(request())["error"]["code"] == "unready"
    assert wake.is_set() and owner.demand_projects() == {"project"}
    owner.publish("project", [{"id": "first"}], observed_at=1)
    assert owner.handle(request())["worktrees"] == [{"id": "first"}]
    owner.publish("project", [], observed_at=2)
    result = owner.handle(request())
    assert result["ok"] is True and result["worktrees"] == []
    assert result["projection"]["revision"] == 2
    owner.shutdown()
    assert owner.handle(request())["error"]["code"] == "closed"


def test_worktrees_runtime_authenticated_transport_and_drain(tmp_path):
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    owner.publish("project", [{"id": "first", "title": "current"}], observed_at=1)
    actions = []

    def handle(action, payload):
        actions.append(action)
        if action == "health":
            return owner.health()
        if action == runtime.READ_ACTION:
            return owner.handle(payload)
        pytest.fail(f"unexpected action {action}")

    server = control.ControlServer(handle, token="fixture-token")
    server.start()
    try:
        client = control.ControlClient(f"http://127.0.0.1:{server.port}", token="fixture-token")
        assert runtime.CAPABILITY in client.health()["capabilities"]
        for _ in range(3):
            result = client.worktrees_read(request(worktree_id="first"))
            assert result["ok"] is True and result["worktrees"] == [
                {"id": "first", "title": "current"}
            ]
            assert result["projection"]["quality"] == "current"
        wrong = control.ControlClient(f"http://127.0.0.1:{server.port}", token="wrong-token")
        with pytest.raises(ValueError):
            wrong.worktrees_read(request())
        assert len(actions) == 4
        owner.close_admission()
        result = client.worktrees_read(request())
        assert result["ok"] is False and result["error"]["code"] == "draining"
        owner.activate()
        assert client.worktrees_read(request())["ok"] is True
    finally:
        server.close()
        owner.shutdown()


def test_worktrees_runtime_old_publisher_cannot_reactivate_after_takeover(tmp_path):
    path = tmp_path / "current.sqlite3"
    old = runtime.WorktreesRuntime(path)
    old.activate()
    old.publish("project", [{"id": "first"}], observed_at=1)
    old.close_admission("superseded")
    new = runtime.WorktreesRuntime(path)
    new.activate()
    assert new.handle(request())["error"]["code"] == "unready"
    with pytest.raises(projection.ProjectionUnavailable, match="superseded"):
        old.activate()
    assert old.handle(request())["ok"] is False
    new.publish("project", [{"id": "second"}], observed_at=2)
    assert new.handle(request())["worktrees"] == [{"id": "second"}]
    assert projection.read_advisory(path, "project")["worktrees"] == [{"id": "second"}]
    old.shutdown()
    new.shutdown()


def test_worktrees_runtime_demand_linger_is_owner_accounting_not_read_compute(tmp_path):
    clock = [10.0]
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3", now=lambda: clock[0])
    owner.activate()
    owner.handle(request())
    clock[0] = 40
    assert owner.demand_projects() == {"project"}
    clock[0] = 40.001
    assert owner.demand_projects() == set()
    owner.shutdown()


def test_worktrees_runtime_failed_producer_keeps_labeled_last_good_rows(tmp_path):
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    owner.publish("project", [{"id": "one"}], observed_at=1)
    owner.publication_failed("project", OSError("producer fixture unavailable"))
    result = owner.handle(request())
    assert result["worktrees"] == [{"id": "one"}]
    assert result["projection"]["revision"] == 1
    assert result["availability"] == {
        "status": "stale",
        "reason": "producer fixture unavailable",
    }
    owner.publish("project", [{"id": "two"}], observed_at=2)
    assert "availability" not in owner.handle(request())
    owner.shutdown()


def test_worktrees_runtime_initial_producer_failure_surfaces_the_actual_reason(tmp_path):
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    owner.publication_failed("project", OSError("fixture producer cannot enumerate mux sessions"))
    result = owner.handle(request())
    assert result["ok"] is False and result["error"] == {
        "code": "unready", "message": "fixture producer cannot enumerate mux sessions",
    }
    owner.shutdown()


def test_worktrees_runtime_late_initial_activation_cannot_steal_new_election(tmp_path):
    path = tmp_path / "current.sqlite3"
    active = runtime.WorktreesRuntime(path)
    active.activate()
    active.publish("project", [{"id": "active"}], observed_at=1)
    late = runtime.WorktreesRuntime(path, elected=lambda: False)
    with pytest.raises(projection.ProjectionUnavailable, match="no longer elected"):
        late.activate()
    assert runtime.owner_generation(path) == active.store.generation
    assert active.handle(request())["worktrees"] == [{"id": "active"}]
    assert late.handle(request())["ok"] is False
    active.shutdown()
    late.shutdown()


def test_worktrees_runtime_elected_activation_rechecks_a_raced_owner_once(tmp_path, monkeypatch):
    path = tmp_path / "current.sqlite3"
    previous = runtime.WorktreesRuntime(path)
    previous.activate()
    observed = iter([None, previous.store.generation])
    monkeypatch.setattr(runtime, "owner_generation", lambda _: next(observed))
    owner = runtime.WorktreesRuntime(path, elected=lambda: True)
    owner.activate()
    owner.publish("project", [{"id": "new"}], observed_at=1)
    assert owner.handle(request())["worktrees"] == [{"id": "new"}]
    with pytest.raises(projection.ProjectionUnavailable, match="superseded"):
        previous.store.publish("project", [{"id": "old"}], observed_at=2)
    previous.shutdown()
    owner.shutdown()


def test_worktrees_runtime_point_wire_bound_uses_utf8_not_ascii_expansion(tmp_path):
    owner = runtime.WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    row = {
        "id": "one",
        **dict.fromkeys(
            (
                "title",
                "summary",
                "activity",
                "branch",
                "path",
                "repo",
                "machine",
                "status",
                "platform",
                "started_at",
                "last_resumed_at",
                "status_note_at",
                "activity_at",
                "live_intent",
                "live_rest",
            ),
            chr(0x754C) * 1365,
        ),
    }
    owner.publish("project", [row], observed_at=1)
    server = control.ControlServer(lambda action, payload: owner.handle(payload), token="fixture")
    server.start()
    try:
        client = control.ControlClient(f"http://127.0.0.1:{server.port}", token="fixture")
        result = client.worktrees_read(request(worktree_id="one"))
        assert result["worktrees"] == [row]
    finally:
        server.close()
        owner.shutdown()


def test_worktrees_runtime_slow_progress_cannot_extend_the_read_deadline():
    class SlowReply(socketserver.StreamRequestHandler):
        def handle(self):
            self.rfile.readline()
            reply = json.dumps({"version": 1, "result": {"ok": True}}).encode() + b"\n"
            try:
                for byte in reply:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(0.03)
            except OSError:
                return

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SlowReply)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = control.ControlClient(
            f"http://127.0.0.1:{server.server_address[1]}",
            token="fixture",
            timeout=0.1,
        )
        with pytest.raises(TimeoutError):
            client.worktrees_read(request())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
