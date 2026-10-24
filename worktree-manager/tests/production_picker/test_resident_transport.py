"""The resident socket path keeps the existing commit and cancellation contract."""

from __future__ import annotations

import json
import os
import socketserver
import threading
from contextlib import contextmanager

import pytest
from zdd import routing

from worktree_manager import resident_picker, resident_transport
from worktree_manager.production_picker.picker_tui import data_ssh
from worktree_manager.worktree_feed_contract import CurrentFeed, FeedUnavailable


def transactions():
    head = {
        "schema_version": 1, "quality": "current", "generation": "a" * 32,
        "revision": 1, "observed_at": 1, "roster_count": 1, "selected_count": 1,
    }
    empty = {**head, "revision": 2, "observed_at": 2, "roster_count": 0, "selected_count": 0}
    return [
        {"type": "begin", "contract_version": 1, "projection": head,
         "selected_count": 1, "cursor": 1, "base_cursor": None},
        {"type": "worktree", "projection": head, "wt": {"id": "one", "title": "Initial"}},
        {"type": "done", "projection": head, "cursor": 1},
        {"type": "update", "contract_version": 1, "projection": empty,
         "selected_count": 0, "cursor": 2, "base_cursor": 1},
        {"type": "delete", "projection": empty, "id": "one"},
        {"type": "commit", "projection": empty, "cursor": 2},
        {"type": "going-away", "generation": "a" * 32, "cursor": 2, "reason": "update"},
    ]


@contextmanager
def server(root, *, mode="normal", values=None, payloads=None):
    started, release = threading.Event(), threading.Event()
    requests = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            raw = self.rfile.readline(8192)
            if not raw:
                return
            request = json.loads(raw)
            requests.append(request["action"])
            if payloads is not None and request["action"] == "worktrees-feed":
                payloads.append(request["payload"])
            if request["token"] != "fixture-token" or mode == "authentication":
                return
            if request["action"] == "health":
                if mode == "held-health":
                    started.set()
                    release.wait(5)
                    return
                capabilities = [] if mode == "old" else ["worktrees-feed-v1"]
                result = {
                    "status": "ready",
                    "capabilities": "worktrees-feed-v1" if mode == "malformed" else capabilities,
                }
                self.wfile.write(json.dumps({"version": 1, "result": result}).encode() + b"\n")
                self.wfile.flush()
                return
            for frame in transactions() if values is None else values:
                self.wfile.write(json.dumps(frame).encode() + b"\n")
                self.wfile.flush()

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

    listener = Server(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=listener.serve_forever, daemon=True)
    worker.start()
    (root / "status-monitor-control.token").write_text("fixture-token", encoding="utf-8")
    routing.publish_active(
        root / "status-monitor-routing", bind="127.0.0.1",
        port=listener.server_address[1], pid=os.getpid(),
    )
    try:
        yield requests, started, release
    finally:
        release.set()
        listener.shutdown()
        listener.server_close()
        worker.join(timeout=2)


def test_socket_transactions_use_the_existing_atomic_receiver(tmp_path):
    receiver = CurrentFeed()
    commits = []
    with server(tmp_path) as (requests, _started, _release):
        for frame in resident_transport.frames(tmp_path, "sample", alive=lambda: True, startup_timeout=2):
            rows = receiver.receive(frame)
            if rows is not None:
                commits.append([row["id"] for row in rows])
    assert commits == [["one"], []]
    assert receiver.notice == "update"
    assert requests == ["health", "worktrees-feed"]


def test_review_reads_real_socket_snapshot_and_closes(tmp_path, monkeypatch):
    from worktree_manager import picker_review

    monkeypatch.setattr(picker_review, "_review", picker_review.Review(
        tmp_path / "candidate", tmp_path, "native-test", ("unused",),
    ))
    with server(tmp_path, values=transactions()[:3]) as (requests, _, _):
        rows = picker_review.worktree_rows("sample")
    assert rows[0]["id"] == "one"
    assert rows[0]["_projection"]["quality"] == "current"
    assert requests == ["health", "worktrees-feed"]


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
def test_socket_feed_preserves_default_cli_selection(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(resident_transport, "detect_platform", lambda: platform)
    payloads = []
    with server(tmp_path, payloads=payloads):
        list(resident_transport.frames(tmp_path, "sample", alive=lambda: True, startup_timeout=2))
    assert payloads == [{
        "contract_version": 1, "project": "sample", "worktree_id": None,
        "codename": None, "status_filter": None,
        "platform_filter": platform, "include_missing": False,
    }]


def test_feed_reads_and_publication_never_call_synchronous_launch_tracing(tmp_path, monkeypatch):
    from worktree_manager.production_picker.picker_tui import frame_health

    source = data_ssh.Source("fixture-host", "Win", None, local=True)
    loader = data_ssh.LiveLoader([source])
    monkeypatch.setattr(
        frame_health, "append_launch_event",
        lambda *_a, **_k: pytest.fail("feed delivery performed synchronous diagnostic I/O"),
    )
    with server(tmp_path):
        for frame in resident_transport.frames(tmp_path, "sample", alive=lambda: True, startup_timeout=2):
            if frame.get("type") == "worktree":
                loader._feed_commit(source, 0, [frame["wt"]])
    assert [row["id"] for row in loader.records()] == ["one"]


def test_local_loader_never_spawns_a_cli_for_an_accepted_resident_binding(tmp_path, monkeypatch):
    monkeypatch.setattr(resident_picker, "root_for", lambda project: tmp_path)
    monkeypatch.setattr(data_ssh, "_project", lambda: "sample")
    source = data_ssh.Source("fixture-host", "Win", None, local=True)
    loader = data_ssh.LiveLoader([source])
    monkeypatch.setattr(loader, "_spawn_stream", lambda *_args: pytest.fail("resident feed spawned a CLI"))
    with server(tmp_path):
        assert loader._feed_once(source, 0) == "resident going away: update"
    assert loader.records() == []
    assert source.cache_key in loader._feed_complete
    assert loader._state[source.cache_key] == "ready"


@pytest.mark.parametrize("mode", ["authentication", "old", "malformed"])
def test_authentication_and_capability_errors_are_terminal(tmp_path, mode):
    with server(tmp_path, mode=mode):
        with pytest.raises(FeedUnavailable) as error:
            list(resident_transport.frames(tmp_path, "sample", alive=lambda: True, startup_timeout=2))
    assert error.value.retry is False


def test_health_cancellation_closes_without_waiting_for_the_startup_budget(tmp_path):
    cancelled = threading.Event()
    errors = []

    def receive():
        try:
            list(resident_transport.frames(
                tmp_path, "sample", alive=lambda: not cancelled.is_set(), startup_timeout=20,
            ))
        except Exception as error:
            errors.append(error)

    with server(tmp_path, mode="held-health") as (_requests, started, _release):
        reader = threading.Thread(target=receive)
        reader.start()
        assert started.wait(2)
        cancelled.set()
        reader.join(timeout=2)
        assert not reader.is_alive()
        assert not errors


def test_live_peer_timeout_is_terminal_and_does_not_extend_the_startup_budget(tmp_path):
    with server(tmp_path, mode="held-health"):
        with pytest.raises(FeedUnavailable) as error:
            list(resident_transport.frames(tmp_path, "sample", alive=lambda: True, startup_timeout=0.05))
    assert error.value.retry is False


def test_incomplete_snapshot_never_replaces_the_receivers_committed_rows(tmp_path):
    receiver = CurrentFeed()
    initial = transactions()[:3]
    for frame in initial:
        receiver.receive(frame)
    before = receiver.rows
    incomplete = transactions()[3:5]
    with server(tmp_path, values=incomplete):
        with pytest.raises(FeedUnavailable):
            for frame in resident_transport.frames(tmp_path, "sample", alive=lambda: True, startup_timeout=2):
                assert receiver.receive(frame) is None
    assert receiver.rows == before
