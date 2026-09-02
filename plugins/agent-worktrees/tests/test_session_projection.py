from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_worktrees import session_projection, sessions, tracking


def _record(tmp_tracking_dir: Path, session_id: str = "session-a"):
    record = tracking.WorktreeRecord(
        worktree_id="wt-a",
        branch="worktree/wt-a",
        worktree_path="/tmp/wt-a",
        repo="example",
        machine="test",
        platform="windows",
        started_at="2026-01-01T00:00:00",
        last_resumed_at="2026-01-01T00:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[tracking.SessionEntry(session_id, "2026-01-01T00:00:00")],
    )
    tracking.save_record(record, tmp_tracking_dir / "wt-a.yaml")
    return record


def _session_root(tmp_path: Path, monkeypatch, session_id: str = "session-a") -> Path:
    root = tmp_path / "session-state"
    session_dir = root / session_id
    session_dir.mkdir(parents=True)
    (session_dir / "workspace.yaml").write_text("cwd: /tmp/wt-a\n", encoding="utf-8")
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: root)
    return session_dir


def test_lifecycle_revision_writes_bound_projection(
    tmp_path, tmp_tracking_dir, monkeypatch, monkeypatch_config
):
    session_dir = _session_root(tmp_path, monkeypatch)
    record = _record(tmp_tracking_dir)

    tracking.set_head_session(record, "session-a")

    loaded = json.loads(
        (session_dir / session_projection.SIDECAR_NAME).read_text(encoding="utf-8")
    )
    assert loaded["version"] == 1
    assert loaded["session_id"] == "session-a"
    assert loaded["overflow"] is False
    assert loaded["omitted_relations"] == 0
    assert loaded["relations"] == [{
        "head_revision": record.head_revision,
        "is_head": True,
        "lifecycle_state": "active",
        "lineage": {
            "handoff_ordinal": None,
            "predecessor": None,
            "successor": None,
        },
        "project": "example",
        "relation_revision": record.session_entry("session-a").relation_revision,
        "role": "bound",
        "worktree_id": "wt-a",
    }]


def test_semantic_noop_does_not_replace_projection(
    tmp_path, tmp_tracking_dir, monkeypatch, monkeypatch_config
):
    session_dir = _session_root(tmp_path, monkeypatch)
    record = _record(tmp_tracking_dir)
    tracking.set_head_session(record, "session-a")
    sidecar = session_dir / session_projection.SIDECAR_NAME
    first_stat = sidecar.stat()

    assert session_projection.sync_bound(record, "session-a") == "current"
    assert sidecar.stat().st_mtime_ns == first_stat.st_mtime_ns


def test_handoff_projects_per_worktree_lineage(
    tmp_path, tmp_tracking_dir, monkeypatch, monkeypatch_config
):
    session_a = _session_root(tmp_path, monkeypatch, "session-a")
    session_b = _session_root(tmp_path, monkeypatch, "session-b")
    record = _record(tmp_tracking_dir)
    record.sessions.append(
        tracking.SessionEntry("session-b", "2026-01-02T00:00:00")
    )
    tracking.open_handoff(record, "session-a", "token", save=False)
    tracking.link_handoff(record, "token", "session-b")

    old = session_projection.read("session-a")
    new = session_projection.read("session-b")
    assert old["relations"][0]["lineage"]["successor"] == "session-b"
    assert old["relations"][0]["lifecycle_state"] == "handed-off"
    assert new["relations"][0]["lineage"]["predecessor"] == "session-a"
    assert new["relations"][0]["is_head"] is True
    assert (session_a / session_projection.SIDECAR_NAME).is_file()
    assert (session_b / session_projection.SIDECAR_NAME).is_file()


def test_newer_projection_is_left_untouched(
    tmp_path, tmp_tracking_dir, monkeypatch, monkeypatch_config
):
    session_dir = _session_root(tmp_path, monkeypatch)
    record = _record(tmp_tracking_dir)
    sidecar = session_dir / session_projection.SIDECAR_NAME
    original = '{"version": 2, "session_id": "session-a", "relations": []}\n'
    sidecar.write_text(original, encoding="utf-8")

    tracking.set_head_session(record, "session-a")

    assert sidecar.read_text(encoding="utf-8") == original


def test_rescue_ingest_marker_is_read_only(
    tmp_path, tmp_tracking_dir, monkeypatch, monkeypatch_config
):
    session_dir = _session_root(tmp_path, monkeypatch)
    (session_dir / "rescued-origin.json").write_text("{}", encoding="utf-8")
    record = _record(tmp_tracking_dir)

    tracking.set_head_session(record, "session-a")

    assert not (session_dir / session_projection.SIDECAR_NAME).exists()


@pytest.mark.parametrize("session_id", ["../escape", "a/b", r"a\b", ".", ".."])
def test_invalid_session_id_is_rejected(tmp_path, monkeypatch, session_id):
    root = tmp_path / "session-state"
    root.mkdir()
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: root)

    with pytest.raises(session_projection.ProjectionError):
        session_projection.read(session_id)


def test_projection_target_symlink_is_rejected(
    tmp_path, tmp_tracking_dir, monkeypatch, monkeypatch_config
):
    session_dir = _session_root(tmp_path, monkeypatch)
    target = session_dir / session_projection.SIDECAR_NAME
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    record = _record(tmp_tracking_dir)

    tracking.set_head_session(record, "session-a")

    assert outside.read_text(encoding="utf-8") == "{}"


def test_read_does_not_create_projection_runtime_dirs(tmp_path, monkeypatch):
    _session_root(tmp_path, monkeypatch)

    assert session_projection.read("session-a") is None
    root = sessions._session_state_dir()
    assert not (root / ".agent-worktrees-locks").exists()
    assert not (root / ".agent-worktrees-tmp").exists()


def test_relation_cap_never_evicts_bound_relation():
    projection = {
        "version": 1,
        "session_id": "session-a",
        "relations": [
            {
                "project": "example",
                "worktree_id": f"controller-{index:03d}",
                "role": "controller",
                "relation_revision": index,
                "relation_state": "finalized",
            }
            for index in range(session_projection.MAX_RELATIONS)
        ],
        "overflow": False,
        "omitted_relations": 0,
    }
    bound = {
        "project": "example",
        "worktree_id": "bound",
        "role": "bound",
    }

    merged = session_projection._merge_relation(projection, bound)

    assert len(merged["relations"]) == session_projection.MAX_RELATIONS
    assert bound in merged["relations"]
    assert merged["overflow"] is True
    assert merged["omitted_relations"] == 1

    updated_bound = dict(bound, relation_revision=2)
    merged_again = session_projection._merge_relation(merged, updated_bound)
    assert merged_again["overflow"] is True
    assert merged_again["omitted_relations"] == 1

    extra = {
        "project": "example",
        "worktree_id": "controller-extra",
        "role": "controller",
    }
    merged_extra = session_projection._merge_relation(merged_again, extra)
    assert merged_extra["overflow"] is True
    assert merged_extra["omitted_relations"] == 2
    retained_ids = {
        relation["worktree_id"] for relation in merged_extra["relations"]
    }
    assert "controller-000" not in retained_ids
    assert "controller-extra" in retained_ids


def test_relations_are_serialized_by_revision_then_identity():
    projection = {
        "version": 1,
        "session_id": "session-a",
        "relations": [
            {
                "project": "example",
                "worktree_id": "later",
                "role": "controller",
                "relation_revision": 9,
            },
            {
                "project": "example",
                "worktree_id": "earlier",
                "role": "controller",
                "relation_revision": 2,
            },
        ],
        "overflow": False,
        "omitted_relations": 0,
    }
    update = {
        "project": "example",
        "worktree_id": "middle",
        "role": "controller",
        "relation_revision": 5,
    }

    merged = session_projection._merge_relation(projection, update)

    assert [
        relation["worktree_id"] for relation in merged["relations"]
    ] == ["earlier", "middle", "later"]


def test_non_object_relation_is_rejected(tmp_path, monkeypatch):
    session_dir = _session_root(tmp_path, monkeypatch)
    (session_dir / session_projection.SIDECAR_NAME).write_text(
        json.dumps({
            "version": 1,
            "session_id": "session-a",
            "relations": ["not-an-object"],
        }),
        encoding="utf-8",
    )

    with pytest.raises(session_projection.ProjectionError):
        session_projection.read("session-a")


def test_stale_relation_revision_cannot_replace_newer():
    newer = {
        "project": "example",
        "worktree_id": "wt-a",
        "role": "bound",
        "relation_revision": 5,
    }
    projection = {
        "version": 1,
        "session_id": "session-a",
        "relations": [newer],
        "overflow": False,
        "omitted_relations": 0,
    }

    stale = dict(newer, relation_revision=4, lifecycle_state="handed-off")

    assert session_projection._merge_relation(projection, stale) == projection


def test_corrupt_projection_is_rebuilt(
    tmp_path, tmp_tracking_dir, monkeypatch, monkeypatch_config
):
    session_dir = _session_root(tmp_path, monkeypatch)
    record = _record(tmp_tracking_dir)
    (session_dir / session_projection.SIDECAR_NAME).write_text(
        "{not-json",
        encoding="utf-8",
    )

    tracking.set_head_session(record, "session-a")

    loaded = session_projection.read("session-a")
    assert loaded is not None
    assert loaded["relations"][0]["worktree_id"] == "wt-a"


def test_oversized_projection_is_rebuilt_with_bounded_read(
    tmp_path, tmp_tracking_dir, monkeypatch, monkeypatch_config
):
    session_dir = _session_root(tmp_path, monkeypatch)
    record = _record(tmp_tracking_dir)
    (session_dir / session_projection.SIDECAR_NAME).write_bytes(
        b"x" * (session_projection.MAX_BYTES + 4096)
    )

    tracking.set_head_session(record, "session-a")

    loaded = session_projection.read("session-a")
    assert loaded is not None
    assert loaded["relations"][0]["worktree_id"] == "wt-a"


def test_handoff_updates_predecessor_when_it_is_not_head(
    tmp_path, tmp_tracking_dir, monkeypatch, monkeypatch_config
):
    _session_root(tmp_path, monkeypatch, "old")
    _session_root(tmp_path, monkeypatch, "current")
    _session_root(tmp_path, monkeypatch, "new")
    record = _record(tmp_tracking_dir, "old")
    record.sessions.extend([
        tracking.SessionEntry("current", "2026-01-02T00:00:00"),
        tracking.SessionEntry("new", "2026-01-03T00:00:00"),
    ])
    tracking.set_head_session(record, "current")
    tracking.open_handoff(record, "old", "token", save=False)

    tracking.link_handoff(record, "token", "new")

    old = session_projection.read("old")
    assert old is not None
    relation = old["relations"][0]
    assert relation["lifecycle_state"] == "handed-off"
    assert relation["lineage"]["successor"] == "new"


def test_concluded_succession_updates_non_head_predecessor(
    tmp_path, tmp_tracking_dir, monkeypatch, monkeypatch_config
):
    _session_root(tmp_path, monkeypatch, "old")
    _session_root(tmp_path, monkeypatch, "current")
    _session_root(tmp_path, monkeypatch, "new")
    record = _record(tmp_tracking_dir, "old")
    record.sessions.extend([
        tracking.SessionEntry("current", "2026-01-02T00:00:00"),
        tracking.SessionEntry("new", "2026-01-03T00:00:00"),
    ])
    tracking.set_head_session(record, "current")

    tracking.link_succession(
        record,
        "old",
        "new",
        predecessor_state="concluded",
    )

    old = session_projection.read("old")
    assert old is not None
    relation = old["relations"][0]
    assert relation["lifecycle_state"] == "concluded"
    assert relation["lineage"]["successor"] == "new"


def test_unrelated_session_sidecar_stays_byte_identical(
    tmp_path, tmp_tracking_dir, monkeypatch, monkeypatch_config
):
    session_a = _session_root(tmp_path, monkeypatch, "session-a")
    _session_root(tmp_path, monkeypatch, "session-b")
    record = _record(tmp_tracking_dir)
    tracking.set_head_session(record, "session-a")
    sidecar_a = session_a / session_projection.SIDECAR_NAME
    before = sidecar_a.read_bytes()

    record.sessions.append(
        tracking.SessionEntry("session-b", "2026-01-02T00:00:00")
    )
    tracking._next_lifecycle_revision(record, "session-b")
    tracking.save_record(record)

    assert sidecar_a.read_bytes() == before
    assert session_projection.read("session-b") is not None


def test_concurrent_projection_updates_do_not_corrupt_sidecar(
    tmp_path, tmp_tracking_dir, monkeypatch, monkeypatch_config
):
    _session_root(tmp_path, monkeypatch)
    record = _record(tmp_tracking_dir)
    tracking.set_head_session(record, "session-a")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _index: session_projection.sync_bound(record, "session-a"),
                range(24),
            )
        )

    loaded = session_projection.read("session-a")
    assert loaded is not None
    assert len(loaded["relations"]) == 1
    assert loaded["relations"][0]["worktree_id"] == "wt-a"
    assert results.count("written") <= 1
    assert set(results) <= {"written", "current"}


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_projection_has_private_permissions(
    tmp_path, tmp_tracking_dir, monkeypatch, monkeypatch_config
):
    session_dir = _session_root(tmp_path, monkeypatch)
    record = _record(tmp_tracking_dir)

    tracking.set_head_session(record, "session-a")

    mode = (session_dir / session_projection.SIDECAR_NAME).stat().st_mode & 0o777
    assert mode == 0o600


def test_projection_temporary_files_stay_outside_session_tree(
    tmp_path, tmp_tracking_dir, monkeypatch, monkeypatch_config
):
    session_dir = _session_root(tmp_path, monkeypatch)
    record = _record(tmp_tracking_dir)

    tracking.set_head_session(record, "session-a")

    assert list(session_dir.glob("*.tmp")) == []
    assert (sessions._session_state_dir() / ".agent-worktrees-tmp").is_dir()


def test_deferred_projection_remains_dirty_for_next_save(
    tmp_path, tmp_tracking_dir, monkeypatch, monkeypatch_config
):
    _session_root(tmp_path, monkeypatch)
    record = _record(tmp_tracking_dir)
    outcomes = iter(["deferred", "written"])
    monkeypatch.setattr(
        session_projection,
        "sync_bound",
        lambda _record, _session_id: next(outcomes),
    )
    tracking._next_lifecycle_revision(record, "session-a")

    tracking.save_record(record)
    assert record._session_projection_dirty == {"session-a"}

    tracking.save_record(record)
    assert record._session_projection_dirty == set()


def test_controller_retraction_salvages_parseable_projection(
    tmp_path, tmp_tracking_dir, monkeypatch, monkeypatch_config
):
    session_dir = _session_root(tmp_path, monkeypatch)
    record = _record(tmp_tracking_dir)
    unrelated_relation = {
        "project": "example",
        "worktree_id": "other",
        "role": "controller",
        "relation_revision": 9,
    }
    unrelated_tombstone = {
        "project": "example",
        "worktree_id": "removed",
        "role": "controller",
        "relation_revision": 8,
    }
    (session_dir / session_projection.SIDECAR_NAME).write_text(
        json.dumps({
            "version": 1,
            "session_id": "session-a",
            "relations": [unrelated_relation, "malformed"],
            "relation_tombstones": [unrelated_tombstone, 42],
            "overflow": False,
            "omitted_relations": 0,
            "future_metadata": {"preserve": True},
        }),
        encoding="utf-8",
    )

    outcome = session_projection.sync_controller(record, "session-a")

    assert outcome == "written"
    rebuilt = session_projection.read("session-a")
    assert rebuilt is not None
    assert rebuilt["relations"] == [unrelated_relation]
    assert unrelated_tombstone in rebuilt["relation_tombstones"]
    assert len(rebuilt["relation_tombstones"]) == 2
    assert rebuilt["future_metadata"] == {"preserve": True}


def test_controller_retraction_defers_unparseable_json(
    tmp_path, tmp_tracking_dir, monkeypatch, monkeypatch_config
):
    session_dir = _session_root(tmp_path, monkeypatch)
    record = _record(tmp_tracking_dir)
    (session_dir / session_projection.SIDECAR_NAME).write_text(
        "{not-json",
        encoding="utf-8",
    )

    assert session_projection.sync_controller(record, "session-a") == "deferred"
