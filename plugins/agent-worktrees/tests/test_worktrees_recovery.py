"""Elected rollback recovery preserves publisher fencing and truthful health."""

from __future__ import annotations

import io
import json
import time

import pytest

from agent_worktrees import status_monitor_cutover as control
from agent_worktrees import worktrees_projection as projection
from agent_worktrees.worktrees_runtime import WorktreesRuntime


def _request():
    return {"contract_version": 1, "project": "project"}


def test_elected_rollback_replaces_fenced_publisher(tmp_path):
    elected = {"old": True, "new": False}
    path = tmp_path / "current.sqlite3"
    old = WorktreesRuntime(path, elected=lambda: elected["old"])
    new = WorktreesRuntime(path, elected=lambda: elected["new"])
    old.activate()
    old.publish("project", [{"id": "before"}], observed_at=1)
    fenced = old.store
    old.close_admission("draining")
    elected.update(old=False, new=True)
    new.activate()
    new.publish("project", [{"id": "replacement"}], observed_at=2)
    with pytest.raises(projection.ProjectionUnavailable):
        old.activate()
    assert old.handle(_request())["ok"] is False

    new.close_admission("draining")
    elected.update(old=True, new=False)
    old.activate()
    assert old.store is not fenced
    assert old.store.generation not in {fenced.generation, new.store.generation}
    old.publish("project", [{"id": "recovered"}], observed_at=3)
    assert old.handle(_request())["worktrees"] == [{"id": "recovered"}]
    assert old.health()["worktrees"]["status"] == "ready"
    with pytest.raises(projection.ProjectionUnavailable):
        fenced.publish("project", [{"id": "obsolete"}], observed_at=4)
    with pytest.raises(projection.ProjectionUnavailable):
        new.publish("project", [{"id": "late"}], observed_at=4)
    assert projection.read_advisory(path, "project")["worktrees"] == [{"id": "recovered"}]
    old.shutdown()
    new.shutdown()


def test_elected_recovery_does_not_reopen_shutdown_runtime(tmp_path):
    owner = WorktreesRuntime(tmp_path / "current.sqlite3", elected=lambda: True)
    owner.activate()
    owner.shutdown()
    with pytest.raises(projection.ProjectionUnavailable, match="closed"):
        owner.activate()


def test_relay_flushes_once_per_committed_transaction(tmp_path, monkeypatch):
    from agent_worktrees import worktrees_feed as feed
    from agent_worktrees import worktrees_feed_client as client

    class Output(io.StringIO):
        flushes = 0

        def flush(self):
            self.flushes += 1

    owner = WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    owner.publish("project", [{"id": "one"}, {"id": "two"}], observed_at=1)
    frames = list(feed.publication_frames(owner.handle(_request())))
    monkeypatch.setattr(client, "_client", lambda request: object())
    monkeypatch.setattr(client, "transport", lambda *args: (frame for frame in frames))
    output = Output()
    monkeypatch.setattr(client.sys, "stdout", output)
    assert client.relay(_request()) == 0
    assert output.flushes == 1
    assert [json.loads(line) for line in output.getvalue().splitlines()] == frames
    owner.shutdown()


def test_real_feed_recovers_across_repeated_elected_rollbacks(tmp_path):
    from agent_worktrees import worktrees_feed_client as client

    elected = [0]
    owners = [
        WorktreesRuntime(tmp_path / "current.sqlite3", elected=lambda index=i: elected[0] == index)
        for i in range(2)
    ]
    servers = [
        control.ControlServer(
            lambda action, payload, owner=owner: (
                owner.health() if action == "health" else owner.handle(payload)
            ),
            token="fixture-token",
            stream_handle=lambda action, payload, disconnected, owner=owner: owner.feed.stream(
                payload, disconnected,
            ),
        )
        for owner in owners
    ]
    try:
        for server in servers:
            server.start()
        owners[0].activate()
        owners[0].publish("project", [{"id": "initial"}], observed_at=1)
        generations = {owners[0].store.generation}
        for cycle in range(10):
            previous = owners[elected[0]]
            previous.close_admission("draining")
            elected[0] = 1 - elected[0]
            owner = owners[elected[0]]
            started = time.monotonic()
            owner.activate()
            owner.publish("project", [{"id": f"current-{cycle}"}], observed_at=cycle + 2)
            assert owner.store.generation not in generations
            generations.add(owner.store.generation)
            server = servers[elected[0]]
            reader = control.ControlClient(
                f"http://127.0.0.1:{server.port}", token="fixture-token",
            )
            stream = client.transport(reader, _request())
            receiver = client.FeedReceiver(_request())
            admitted = None
            try:
                for frame in stream:
                    admitted = receiver.receive(frame)
                    if admitted:
                        break
            finally:
                stream.close()
            assert admitted[-1]["type"] == "done"
            assert [
                frame["wt"]["id"] for frame in admitted if frame["type"] == "worktree"
            ] == [f"current-{cycle}"]
            assert time.monotonic() - started < 5
            with pytest.raises(projection.ProjectionUnavailable):
                previous.store.publish("project", [{"id": "late"}], observed_at=100)
        durations = []
        for _ in range(100):
            started = time.monotonic()
            assert reader.worktrees_read(_request())["worktrees"] == [{"id": "current-9"}]
            durations.append(time.monotonic() - started)
        assert sorted(durations)[94] <= 0.5
        print(
            f"100 native current reads: p50={sorted(durations)[49]:.4f}s "
            f"p95={sorted(durations)[94]:.4f}s max={max(durations):.4f}s; 10 rollbacks",
        )
    finally:
        for owner in owners:
            owner.close_admission()
        for server in servers:
            server.close()
        for owner in owners:
            owner.shutdown()


@pytest.mark.parametrize(
    ("published", "roster_status", "healthy"),
    [(True, "unavailable", False), (True, "unready", False),
     (True, "ready", True), (False, "unready", True),
     (False, "unavailable", False)],
)
def test_cutover_health_checks_roster_serviceability(published, roster_status, healthy):
    server = control.ControlServer(
        lambda action, payload: {
            "status": "ready", "published": published,
            "worktrees": {"status": roster_status},
        },
        token="fixture-token",
    )
    server.start()
    try:
        original = control.ControlClient

        class Client(original):
            def __init__(self, url):
                super().__init__(url, token="fixture-token")

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(control, "ControlClient", Client)
            assert control._health_check("127.0.0.1", server.port) is healthy
    finally:
        server.close()
