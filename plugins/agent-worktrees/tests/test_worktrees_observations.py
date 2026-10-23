"""Semantic writes, real producer observations and negotiated feed compatibility."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import replace

import pytest

from agent_worktrees import __main__ as core
from agent_worktrees import git_ops, reclaim, sessions, tracking
from agent_worktrees import worktrees_observation_contract as contract
from agent_worktrees import worktrees_producer as producer
from agent_worktrees.worktrees_client import validate_result
from agent_worktrees.worktrees_feed import publication_frames
from agent_worktrees.worktrees_feed_client import FeedReceiver
from agent_worktrees.worktrees_projection import WorktreesProjection, read_advisory
from agent_worktrees.worktrees_projection_index import ProjectionError, ProjectionUnavailable
from agent_worktrees.worktrees_runtime import WorktreesRuntime, request_selection


def request(version=2):
    return {"contract_version": version, "project": "project"}


def row(title="Title", at=10, quality="current"):
    return {
        "id": "one", "title": title, "activity_at": "important-time",
        "fact_provenance": {"session": {"quality": quality, "observed_at": at}},
    }


def collect(receiver, result, previous=None):
    frames = list(publication_frames(result, previous))
    admitted = []
    for frame in frames:
        admitted.extend(receiver.receive(frame) or [])
    assert admitted == frames
    return frames


def audit_semantic_writes(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE test_row_writes (operation TEXT, id TEXT)")
        for op in ("INSERT", "UPDATE", "DELETE"):
            ref = "old" if op == "DELETE" else "new"
            conn.execute(
                f"CREATE TRIGGER test_{op.lower()} AFTER {op} ON worktrees_projection_row "
                f"BEGIN INSERT INTO test_row_writes VALUES ('{op}', {ref}.worktree_id); END"
            )


def writes(path):
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT operation, id FROM test_row_writes").fetchall()


@pytest.mark.timeout(180)
def test_real_producer_thousand_rows_ten_observations_and_one_title(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").write_text("gitdir: fixture")
    config = producer.cfg.project_dir("project") / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"repos": {"project": {"anchor": str(checkout)}}}))
    seed = tracking.WorktreeRecord(
        worktree_id="tree-0000", branch="worktree/fixture",
        worktree_path=str(checkout), repo="project", machine="host", platform="windows",
        title="Current task", started_at="2026-10-13T00:00:00",
        last_resumed_at="2026-10-13T00:00:00", resume_count=0,
        status="active", completed_at=None,
    )
    records = [replace(seed, worktree_id=f"tree-{i:04d}") for i in range(1000)]
    monkeypatch.setattr(producer.cfg, "project_name", lambda: "project")
    monkeypatch.setattr(tracking, "list_records", lambda *a, **kw: records)
    monkeypatch.setattr(sessions, "scan_sessions_fast", lambda _: sessions.SessionContext())
    monkeypatch.setattr(sessions, "mux_status_many", lambda _: {
        rec.worktree_id: sessions.MuxInfo(exists=False, clients=0) for rec in records
    })
    monkeypatch.setattr(core, "_classify_records_live", lambda *a, **kw: {
        rec.worktree_id: git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.WIP, dirty=4)
        for rec in records
    })
    monkeypatch.setattr(reclaim, "bare_orphan_worktree_ids", lambda: set())
    monkeypatch.setattr(reclaim, "live_bridge_worktrees", lambda: set())
    clock = [100.0]
    monkeypatch.setattr(producer.time, "time", lambda: clock[0])
    owner = WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    publication_counts = []
    qualified = False
    try:
        producer.publish_active_project(owner)
        previous = owner.handle(request())
        receiver = FeedReceiver(request())
        collect(receiver, previous)
        audit_semantic_writes(owner.store.db_path)
        index = owner.store._projects["project"].index
        for _ in range(10):
            clock[0] += 1
            assert producer.publish_active_project(owner) == 1000
            current = validate_result(owner.handle(request()), request())
            frames = collect(receiver, current, previous)
            publication_counts.append({
                "revision": current["projection"]["revision"],
                "semantic_frames": sum(f["type"] == "worktree" for f in frames),
                "observation_frames": sum(f["type"] == "observation" for f in frames),
                "semantic_writes": len(writes(owner.store.db_path)),
            })
            assert sum(f["type"] == "worktree" for f in frames) == 0, {
                key: (previous["worktrees"][0].get(key), value)
                for key, value in current["worktrees"][0].items()
                if previous["worktrees"][0].get(key) != value
            }
            assert sum(f["type"] == "observation" for f in frames) == 1000
            assert current["worktrees"] == previous["worktrees"]
            assert current["observations"][0]["facts"]["session"]["observed_at"] == clock[0]
            assert owner.handle(request(1))["worktrees"][0]["fact_provenance"]["session"][
                "observed_at"
            ] == clock[0]
            previous = current
        assert writes(owner.store.db_path) == []
        assert owner.store._projects["project"].index is index
        records[17] = replace(records[17], title="One changed title")
        clock[0] += 1
        producer.publish_active_project(owner)
        current = owner.handle(request())
        frames = collect(receiver, current, previous)
        assert [f["wt"]["id"] for f in frames if f["type"] == "worktree"] == ["tree-0017"]
        assert writes(owner.store.db_path) == [("UPDATE", "tree-0017")]
        assert owner.store._projects["project"].index is index
        advisory = read_advisory(owner.store.db_path, "project", contract_version=2)
        assert advisory["worktrees"] == current["worktrees"]
        assert advisory["observations"] == current["observations"]
        qualified = True
    finally:
        if report_dir := os.environ.get("F1_NATIVE_REPORT_DIR"):
            from pathlib import Path

            report = Path(report_dir).resolve()
            assert report.is_relative_to(Path(__file__).resolve().parents[3] / ".test-venvs")
            (report / "semantic-publications.json").write_text(json.dumps({
                "source_commit": os.environ.get("F1_SOURCE_COMMIT"),
                "fixture_rows": 1000, "unchanged_publications": publication_counts,
                "qualified": qualified,
                "final_semantic_writes": writes(owner.store.db_path) if publication_counts else [],
            }, indent=2), encoding="utf-8")
        owner.shutdown()


def test_observation_heartbeat_order_delete_and_reconnect(tmp_path):
    owner = WorktreesRuntime(tmp_path / "rows.sqlite3")
    owner.activate()
    try:
        owner.publish("project", [row(), {**row(), "id": "two"}], observed_at=10)
        receiver = FeedReceiver(request())
        first = owner.handle(request())
        collect(receiver, first)
        heartbeat = {"type": "heartbeat", "generation": first["projection"]["generation"],
                     "cursor": 1}
        assert receiver.receive(heartbeat) == [heartbeat]
        assert receiver.current["observations"] == first["observations"]
        owner.publish("project", [{**row(at=11), "id": "two"}, row(at=11)], observed_at=11)
        second = owner.handle(request())
        frames = collect(receiver, second, first)
        assert not any(f["type"] == "worktree" for f in frames)
        assert [r["id"] for r in receiver.current["worktrees"]] == ["two", "one"]
        owner.publish("project", [row(at=None, quality="unavailable")], observed_at=12)
        third = owner.handle(request())
        collect(receiver, third, second)
        assert receiver.current["observations"][0]["facts"]["session"]["observed_at"] is None
        reconnect = FeedReceiver(request())
        collect(reconnect, third)
        assert reconnect.current["worktrees"] == receiver.current["worktrees"]
        assert read_advisory(owner.store.db_path, "project")["worktrees"][0][
            "fact_provenance"
        ]["session"]["observed_at"] is None
    finally:
        owner.shutdown()


def test_failed_observation_commit_preserves_all_publication_state(tmp_path):
    store = WorktreesProjection(tmp_path / "rows.sqlite3")
    store.activate()
    store.publish("project", [row()], observed_at=10)
    before = store.read("project", contract_version=2)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "CREATE TRIGGER fail_observation BEFORE UPDATE ON worktrees_projection_observation "
            "BEGIN SELECT RAISE(ABORT, 'fixture failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.publish("project", [row(title="after", at=11)], observed_at=11)
    assert store.read("project", contract_version=2) == before
    durable = read_advisory(store.db_path, "project", contract_version=2)
    assert durable["worktrees"] == before["worktrees"]
    assert durable["observations"] == before["observations"]
    assert durable["projection"]["revision"] == 1


def test_compatible_older_writer_can_elect_and_publish_over_additive_tables(tmp_path):
    store = WorktreesProjection(tmp_path / "rows.sqlite3")
    store.activate()
    store.publish("project", [row()], observed_at=10)
    # The pre-extension writer's exact SQL shape still works. It does not know
    # about observation tables, and a different incarnation fences their data.
    from agent_worktrees.worktrees_projection_index import SelectionIndex

    legacy = row(title="Legacy elected writer", at=20)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT OR REPLACE INTO worktrees_projection_owner VALUES (1, ?, ?)",
                     ("b" * 32, "legacy-writer"))
        conn.execute("DELETE FROM worktrees_projection_row WHERE project=?", ("project",))
        conn.execute("INSERT INTO worktrees_projection_row VALUES (?, ?, ?)",
                     ("project", "one", json.dumps(legacy)))
        conn.execute("INSERT OR REPLACE INTO worktrees_projection_head VALUES (?, ?, ?, ?, ?, ?)",
                     ("project", 1, "b" * 32, 1, 20, 1))
        SelectionIndex([legacy]).persist(conn, "project")
    durable = read_advisory(store.db_path, "project", contract_version=2)
    assert durable["projection"]["generation"] == "b" * 32
    assert durable["worktrees"][0]["title"] == "Legacy elected writer"
    assert durable["observations"][0]["facts"]["session"]["observed_at"] == 20
    with pytest.raises(ProjectionUnavailable, match="superseded"):
        store.publish("project", [row(at=30)], observed_at=30)
    newer = WorktreesProjection(store.db_path)
    newer.activate(expected_generation="b" * 32)
    newer.publish("project", [row(at=30)], observed_at=30)
    assert newer.read("project", contract_version=2)["projection"]["revision"] == 1


def test_negotiation_and_observation_validation():
    assert contract.negotiate(request(), [])["contract_version"] == 1
    assert contract.negotiate(request(1), [contract.CAPABILITY])["contract_version"] == 2
    assert request_selection(request())[0] == "project"
    with pytest.raises(ProjectionError):
        request_selection({**request(), "refresh": True})
    value = contract.observation(row(), 10)
    assert contract.observation(row(), 11, value) == value
    with pytest.raises(ProjectionError):
        contract.observation(row(at=10, quality="unavailable"), 11)


@pytest.mark.parametrize("version", [1, 2])
def test_negotiated_observations_cross_real_authenticated_control_socket(tmp_path, version):
    from agent_worktrees import status_monitor_cutover as control
    from agent_worktrees.worktrees_feed_client import transport

    owner = WorktreesRuntime(tmp_path / "rows.sqlite3")
    owner.activate()
    owner.publish("project", [row()], observed_at=10)
    server = control.ControlServer(
        lambda action, payload: owner.health() if action == "health" else owner.handle(payload),
        token="fixture",
        stream_handle=lambda action, payload, disconnected: owner.feed.stream(payload, disconnected),
    )
    server.start()
    reader = control.ControlClient(f"http://127.0.0.1:{server.port}", token="fixture", timeout=2)
    selected = contract.negotiate(
        request(1), reader.health()["capabilities"] if version == 2 else [],
    )
    stream = transport(reader, selected)
    receiver = FeedReceiver(selected)

    def next_transaction():
        result = []
        for frame in stream:
            result.append(frame)
            receiver.receive(frame)
            if frame["type"] in {"done", "commit"}:
                return result
        pytest.fail("missing publication barrier")

    try:
        next_transaction()
        owner.publish("project", [row(at=11)], observed_at=11)
        frames = next_transaction()
        assert sum(frame["type"] == "worktree" for frame in frames) == (version == 1)
        assert sum(frame["type"] == "observation" for frame in frames) == (version == 2)
        assert receiver.current["projection"]["revision"] == 2
    finally:
        stream.close()
        owner.shutdown()
        server.close()


@pytest.mark.parametrize("damage", ["missing", "duplicate", "quality", "future", "semantic"])
def test_malformed_observations_are_not_committed(tmp_path, damage):
    owner = WorktreesRuntime(tmp_path / "rows.sqlite3")
    owner.activate()
    try:
        owner.publish("project", [row()], observed_at=10)
        current = owner.handle(request())
        if damage == "missing":
            current["observations"] = []
        elif damage == "duplicate":
            current["observations"] *= 2
        elif damage == "quality":
            current["observations"][0]["facts"]["session"]["quality"] = "cached"
        elif damage == "future":
            current["observations"][0]["facts"]["session"]["observation_revision"] = 2
        else:
            current["worktrees"][0]["semantic_revision"] = True
        with pytest.raises(ProjectionUnavailable):
            validate_result(current, request())
    finally:
        owner.shutdown()
