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


def wire_bytes(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def sized_row(size, revision, *, non_ascii=False, facts=False):
    value = {"id": "one", "claims_assets": {
        f"padding{i}": "é" * 2048 if non_ascii else "x" * 4096 for i in range(15)
    }}
    if facts:
        value["fact_provenance"] = {
            name: {"quality": "current", "observed_at": 1} for name in contract.FACTS
        }
    value["claims_assets"]["tail"] = ""
    value["claims_assets"]["tail"] = "x" * (
        size - wire_bytes({**contract.semantic_row(value), "semantic_revision": revision})
    )
    assert len(value["claims_assets"]["tail"]) <= 4096
    assert wire_bytes({**contract.semantic_row(value), "semantic_revision": revision}) == size
    return value


@pytest.fixture
def manager_contract(monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "worktree-manager" / "src"))
    from worktree_manager.worktree_feed_contract import CurrentFeed
    from worktree_manager.worktree_rows_contract import parse_current_rows

    return CurrentFeed, parse_current_rows


def publication_tables(path):
    with sqlite3.connect(path) as conn:
        names = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'worktrees_projection_%' ORDER BY name",
        ).fetchall()
        return {name: sorted(conn.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr)
                for (name,) in names}


def before_revision(owner, revision):
    if revision == 1:
        return
    owner.publish("project", [{"id": "one", "title": "before"}], observed_at=1)
    if revision <= 10:
        for index in range(2, revision):
            owner.publish("project", [{"id": "one", "title": f"before-{index}"}], observed_at=1)
    else:
        # Seed a valid signed-SQLite revision boundary without billions of waves.
        owner.store._projects["project"] = replace(
            owner.store._projects["project"], revision=revision - 1,
        )
        with sqlite3.connect(owner.store.db_path) as conn:
            for table in ("head", "observation_head"):
                conn.execute(
                    f"UPDATE worktrees_projection_{table} SET revision=? WHERE project=?",
                    (revision - 1, "project"),
                )


def test_complete_wire_budget_rejects_65558_before_durable_publication(tmp_path, manager_contract):
    owner = WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    before = publication_tables(owner.store.db_path)
    value = sized_row(65558, 1)
    assert wire_bytes(value) == 65536
    try:
        try:
            owner.publish("project", [value], observed_at=1)
        except ProjectionError as exc:
            assert "byte bound" in str(exc)
        else:
            result = owner.handle(request())
            assert wire_bytes(result["worktrees"][0]) == 65558
            with pytest.raises(ValueError, match="byte bound"):
                manager_contract[1](result)
            pytest.fail("publisher committed 65536 stored bytes / 65558 v2 bytes rejected by Manager")
        assert publication_tables(owner.store.db_path) == before
        with pytest.raises(ProjectionUnavailable):
            owner.store.read("project")
    finally:
        owner.shutdown()


@pytest.mark.parametrize("revision", [1, 9, 10, 2**63 - 1])
@pytest.mark.parametrize("non_ascii", [False, True])
@pytest.mark.parametrize("size", [65536, 65537])
def test_complete_wire_budget_real_publication_to_manager(
    tmp_path, manager_contract, revision, non_ascii, size,
):
    owner = WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    before_revision(owner, revision)
    owner.publish("other", [{"id": "other"}], observed_at=1)
    before = publication_tables(owner.store.db_path)
    previous = owner.store._projects.get("project")
    value = sized_row(size, revision, non_ascii=non_ascii)
    try:
        if size == 65537:
            with pytest.raises(ProjectionError, match="byte bound"):
                owner.publish("project", [{"id": "two", "title": "must not commit"}, value],
                              observed_at=2)
            assert publication_tables(owner.store.db_path) == before
            assert owner.store._projects.get("project") is previous
            return
        owner.publish("project", [value], observed_at=2)
        result = json.loads(json.dumps(owner.handle(request()), ensure_ascii=False))
        assert result["projection"]["revision"] == revision
        assert result["worktrees"][0]["semantic_revision"] == revision
        assert wire_bytes(result["worktrees"][0]) == 65536
        assert validate_result(result, request()) == result
        parsed = manager_contract[1](result)[0]
        assert wire_bytes(parsed) > 65536
        engine, manager = FeedReceiver(request()), manager_contract[0]()
        for frame in publication_frames(result):
            decoded = json.loads(json.dumps(frame, ensure_ascii=False, separators=(",", ":")))
            engine.receive(decoded)
            manager.receive(decoded)
        assert engine.current["worktrees"] == result["worktrees"]
        assert manager.rows[0] == parsed
        assert read_advisory(owner.store.db_path, "project", contract_version=2)[
            "worktrees"
        ] == result["worktrees"]
        assert owner.handle(request(1))["worktrees"] == [value]
    finally:
        owner.shutdown()


def test_complete_wire_budget_digit_growth_and_noop_semantic_revision(tmp_path, manager_contract):
    owner = WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    before_revision(owner, 9)
    value = sized_row(65536, 9, non_ascii=True)
    owner.publish("project", [value], observed_at=2)
    before = owner.handle(request())
    durable = publication_tables(owner.store.db_path)
    changed = {**value, "claims_assets": {**value["claims_assets"]}}
    changed["claims_assets"]["tail"] = "y" + changed["claims_assets"]["tail"][1:]
    assert wire_bytes({**changed, "semantic_revision": 10}) == 65537
    audit_semantic_writes(owner.store.db_path)
    try:
        with pytest.raises(ProjectionError, match="byte bound"):
            owner.publish("project", [{"id": "two"}, changed], observed_at=3)
        assert publication_tables(owner.store.db_path) == durable
        assert owner.store.read("project", contract_version=2)["worktrees"] == before["worktrees"]
        assert writes(owner.store.db_path) == []
        owner.publish("project", [value], observed_at=3)
        current = owner.handle(request())
        assert current["projection"]["revision"] == 10
        assert current["worktrees"][0]["semantic_revision"] == 9
        assert wire_bytes(current["worktrees"][0]) == 65536
        frames = list(publication_frames(current, before))
        assert sum(frame["type"] == "worktree" for frame in frames) == 0
        assert writes(owner.store.db_path) == []
        manager = manager_contract[0]()
        for publication, previous in ((before, None), (current, before)):
            for frame in publication_frames(publication, previous):
                manager.receive(json.loads(json.dumps(frame, ensure_ascii=False)))
        assert manager.rows[0]["semantic_revision"] == 9
        changed["claims_assets"]["tail"] = changed["claims_assets"]["tail"][:-1]
        owner.publish("project", [changed], observed_at=4)
        final = owner.handle(request())
        assert final["worktrees"][0]["semantic_revision"] == 11
        assert wire_bytes(final["worktrees"][0]) == 65536
        frames = list(publication_frames(final, current))
        assert sum(frame["type"] == "worktree" for frame in frames) == 1
        assert writes(owner.store.db_path) == [("UPDATE", "one")]
        for frame in frames:
            manager.receive(json.loads(json.dumps(frame, ensure_ascii=False)))
        assert manager.rows[0]["semantic_revision"] == 11
    finally:
        owner.shutdown()


@pytest.mark.parametrize("revision", [1, 10, 2**63 - 1])
def test_complete_wire_budget_both_engine_admissions_reject_raw_oversize(
    tmp_path, manager_contract, revision,
):
    owner = WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    owner.publish("project", [{"id": "one"}], observed_at=1)
    try:
        result = owner.handle(request())
        result["projection"]["revision"] = revision
        result["worktrees"] = [{**sized_row(65537, revision, non_ascii=True),
                                "semantic_revision": revision}]
        with pytest.raises(ProjectionUnavailable, match="byte bound"):
            validate_result(result, request())
        with pytest.raises(ValueError, match="byte bound"):
            manager_contract[1](result)
        receiver = FeedReceiver(request())
        frames = list(publication_frames(result))
        receiver.receive(frames[0])
        with pytest.raises(ProjectionError, match="byte bound"):
            receiver.receive(frames[1])
        assert receiver.current is None
    finally:
        owner.shutdown()


def test_complete_wire_budget_keeps_full_v1_capacity_with_observation_metadata(tmp_path):
    owner = WorktreesRuntime(tmp_path / "current.sqlite3")
    owner.activate()
    value = sized_row(65536, 1, facts=True)
    shrink = wire_bytes(value) - 65536
    value["claims_assets"]["tail"] = value["claims_assets"]["tail"][:-shrink]
    assert wire_bytes(value) == 65536
    try:
        owner.publish("project", [value], observed_at=1)
        assert owner.handle(request(1))["worktrees"] == [value]
        assert wire_bytes(owner.handle(request())["worktrees"][0]) < 65536
        assert read_advisory(owner.store.db_path, "project")["worktrees"] == [value]
    finally:
        owner.shutdown()


def test_complete_wire_budget_older_writer_keeps_full_v1_capacity(tmp_path):
    from agent_worktrees.worktrees_projection_index import SelectionIndex

    store = WorktreesProjection(tmp_path / "legacy.sqlite3")
    store.activate()
    value = sized_row(65558, 1, non_ascii=True)
    assert wire_bytes(value) == 65536
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("INSERT OR REPLACE INTO worktrees_projection_owner VALUES (1, ?, ?)",
                     ("b" * 32, "legacy-writer"))
        conn.execute("INSERT INTO worktrees_projection_row VALUES (?, ?, ?)",
                     ("project", "one", json.dumps(
                         value, ensure_ascii=False, separators=(",", ":"),
                     )))
        conn.execute("INSERT INTO worktrees_projection_head VALUES (?, ?, ?, ?, ?, ?)",
                     ("project", 1, "b" * 32, 1, 1, 1))
        SelectionIndex([value]).persist(conn, "project")
    before = publication_tables(store.db_path)
    assert read_advisory(store.db_path, "project", contract_version=1)["worktrees"] == [value]
    with pytest.raises(ProjectionError, match="byte bound"):
        read_advisory(store.db_path, "project", contract_version=2)
    assert publication_tables(store.db_path) == before


@pytest.mark.parametrize("computed_at", [None, "2026-10-24T01:00:01Z"])
def test_closure_observation_reconstruction_drops_removed_timestamp(
    tmp_path, manager_contract, computed_at,
):
    owner = WorktreesRuntime(tmp_path / "closure.sqlite3")
    owner.activate()
    first = {**row(), "closure": {"eligible": False, "computed_at": "2026-10-24T01:00:00Z"}}
    current = {**row(), "closure": {"eligible": False}}
    if computed_at is not None:
        current["closure"]["computed_at"] = computed_at
    try:
        owner.publish("project", [first], observed_at=10)
        previous = {version: owner.handle(request(version)) for version in (1, 2)}
        retained = owner.store._projects["project"].rows["one"]
        audit_semantic_writes(owner.store.db_path)
        owner.publish("project", [current], observed_at=11)
        publications = {version: owner.handle(request(version)) for version in (1, 2)}
        assert owner.store._projects["project"].rows["one"] == retained
        assert writes(owner.store.db_path) == []
        assert publications[2]["worktrees"][0]["semantic_revision"] == 1
        for result in (
            publications[1], read_advisory(owner.store.db_path, "project", contract_version=1),
        ):
            assert result["worktrees"] == [current]
        for version in (1, 2):
            engine, manager = FeedReceiver(request(version)), manager_contract[0]()
            collect(engine, previous[version])
            frames = collect(engine, publications[version], previous[version])
            assert sum(frame["type"] == "worktree" for frame in frames) == (version == 1)
            for value, old in ((previous[version], None),
                               (publications[version], previous[version])):
                for frame in publication_frames(value, old):
                    manager.receive(json.loads(json.dumps(frame)))
            assert engine.current["worktrees"][0]["closure"] == (
                current["closure"] if version == 1 else {"eligible": False}
            )
            assert manager.rows[0]["closure"] == engine.current["worktrees"][0]["closure"]
        observations = publications[2]["observations"][0]
        assert ("derivations" in observations) == (computed_at is not None)
        if computed_at is not None:
            assert observations["derivations"]["closure"]["computed_at"] == computed_at
    finally:
        owner.shutdown()


@pytest.mark.parametrize("clear_final", [False, True])
def test_publication_snapshots_reusable_row_generator(tmp_path, manager_contract, clear_final):
    owner = WorktreesRuntime(tmp_path / "generator.sqlite3")
    owner.activate()
    receivers = {version: FeedReceiver(request(version)) for version in (1, 2)}
    managers = {version: manager_contract[0]() for version in (1, 2)}
    previous = {1: None, 2: None}

    def batch(at):
        shared = row()
        shared["closure"] = {"eligible": False}
        for index, wid in enumerate(("one", "two"), start=1):
            shared["id"] = wid
            shared["title"] = f"Title-{wid}"
            shared["fact_provenance"]["session"]["observed_at"] = at + index
            shared["closure"]["computed_at"] = f"2026-10-24T00:00:{at + index:02d}Z"
            yield shared
        if clear_final:
            shared.clear()

    try:
        for wave, at in enumerate((10, 20), start=1):
            owner.publish("project", batch(at), observed_at=at)
            for version in (1, 2):
                result = validate_result(owner.handle(request(version)), request(version))
                assert [value["id"] for value in result["worktrees"]] == ["one", "two"]
                if version == 1:
                    assert [value["fact_provenance"]["session"]["observed_at"]
                            for value in result["worktrees"]] == [at + 1, at + 2]
                    assert [value["closure"]["computed_at"] for value in result["worktrees"]] == [
                        f"2026-10-24T00:00:{at + index:02d}Z" for index in (1, 2)
                    ]
                else:
                    assert [value["id"] for value in result["observations"]] == ["one", "two"]
                    assert [value["facts"]["session"]["observed_at"]
                            for value in result["observations"]] == [at + 1, at + 2]
                    assert [value["semantic_revision"] for value in result["worktrees"]] == [1, 1]
                frames = collect(receivers[version], result, previous[version])
                for frame in frames:
                    managers[version].receive(json.loads(json.dumps(frame)))
                assert [value["id"] for value in managers[version].rows] == ["one", "two"]
                assert read_advisory(owner.store.db_path, "project", contract_version=version)[
                    "worktrees"
                ] == result["worktrees"]
                if wave == 2 and version == 2:
                    assert sum(frame["type"] == "worktree" for frame in frames) == 0
                    assert sum(frame["type"] == "observation" for frame in frames) == 2
                previous[version] = result
            if wave == 1:
                audit_semantic_writes(owner.store.db_path)
        assert writes(owner.store.db_path) == []
    finally:
        owner.shutdown()


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
