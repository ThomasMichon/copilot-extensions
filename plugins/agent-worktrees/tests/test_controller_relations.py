from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from agent_worktrees import __main__ as cli
from agent_worktrees import session_projection, sessions, tracking
from agent_worktrees.picker_tui import derive


def _session_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *session_ids: str,
) -> Path:
    root = tmp_path / "session-state"
    for session_id in session_ids:
        session_dir = root / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "workspace.yaml").write_text(
            "cwd: /tmp/example\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: root)
    return root


def _record(
    tracking_dir: Path,
    worktree_id: str,
    *,
    sessions_list: list[tracking.SessionEntry] | None = None,
) -> tracking.WorktreeRecord:
    record = tracking.WorktreeRecord(
        worktree_id=worktree_id,
        branch=f"worktree/{worktree_id}",
        worktree_path=f"/tmp/{worktree_id}",
        repo="example",
        machine="host",
        platform="windows",
        started_at="2026-01-01T00:00:00",
        last_resumed_at="2026-01-01T00:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=sessions_list if sessions_list is not None else [],
    )
    tracking.save_record(record, tracking_dir / f"{worktree_id}.yaml")
    return record


def test_empty_controller_model_preserves_legacy_bytes(tmp_path: Path) -> None:
    path = tmp_path / "worktree.yaml"
    record = _record(tmp_path, "worktree")
    before = path.read_bytes()

    loaded = tracking.load_record(path)
    tracking.save_record(loaded, path)

    assert path.read_bytes() == before
    assert loaded.controller_revision == 0
    assert loaded.controllers == []
    assert b"controller" not in before
    assert record.resolved_head_session is None


def test_legacy_creation_metadata_stays_read_only_until_explicit_backfill(
    tmp_path: Path,
) -> None:
    path = tmp_path / "child.yaml"
    path.write_text(
        "worktree_id: child\n"
        "branch: worktree/child\n"
        "worktree_path: /tmp/child\n"
        "repo: example\n"
        "machine: host\n"
        "platform: windows\n"
        "started_at: 2026-01-01T00:00:00\n"
        "last_resumed_at: 2026-01-01T00:00:00\n"
        "resume_count: 0\n"
        "title: null\n"
        "status: active\n"
        "completed_at: null\n"
        "parent_session: controller-session\n"
        "caller_worktree: parent\n"
        "owner_ref: host/example/parent#controller-session\n"
        "sessions: []\n",
        encoding="utf-8",
    )

    record = tracking.load_record(path)

    assert record.controller_revision == 0
    assert record.controllers == []
    assert record.resolved_head_session is None

    tracking.save_record(record, path)
    migrated = path.read_text(encoding="utf-8")
    assert "controllers:" not in migrated
    assert "controller_revision:" not in migrated
    assert "parent_session: controller-session" in migrated
    assert "caller_worktree: parent" in migrated
    assert "owner_ref: host/example/parent#controller-session" in migrated


def test_creation_projects_one_controller_across_multiple_children(
    tmp_path: Path,
    tmp_tracking_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    monkeypatch_config,
) -> None:
    root = _session_root(tmp_path, monkeypatch, "controller-session")
    parent = _record(
        tmp_tracking_dir,
        "parent",
        sessions_list=[
            tracking.SessionEntry(
                "controller-session",
                "2026-01-01T00:00:00",
            )
        ],
    )
    tracking.set_head_session(parent, "controller-session")

    children = [
        tracking.create_new_record(
            child,
            f"worktree/{child}",
            f"/tmp/{child}",
            "example",
            "host",
            "windows",
            tmp_tracking_dir,
            parent_session="controller-session",
            caller_worktree="parent",
            owner_ref="host/example/parent#controller-session",
        )
        for child in ("child-a", "child-b")
    ]

    assert all(child.sessions == [] for child in children)
    assert all(child.resolved_head_session is None for child in children)
    projection = json.loads(
        (
            root
            / "controller-session"
            / session_projection.SIDECAR_NAME
        ).read_text(encoding="utf-8")
    )
    assert {
        (relation["worktree_id"], relation["role"])
        for relation in projection["relations"]
    } == {
        ("parent", "bound"),
        ("child-a", "controller"),
        ("child-b", "controller"),
    }


def test_cross_project_owner_folds_matching_bare_caller(
    tmp_tracking_dir: Path,
) -> None:
    child = tracking.create_new_record(
        "child",
        "worktree/child",
        "/tmp/child",
        "target-project",
        "host",
        "windows",
        tmp_tracking_dir,
        parent_session="controller-session",
        caller_worktree="parent",
        owner_ref="host/source-project/parent#controller-session",
    )

    assert len(child.controllers) == 1
    assert child.controllers[0].controller_ref == (
        "host/source-project/parent#controller-session"
    )
    assert child.controllers[0].source == "owner-ref"


def test_controller_end_and_removal_preserve_bound_relation(
    tmp_path: Path,
    tmp_tracking_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    monkeypatch_config,
) -> None:
    _session_root(tmp_path, monkeypatch, "controller-session")
    parent = _record(
        tmp_tracking_dir,
        "parent",
        sessions_list=[
            tracking.SessionEntry(
                "controller-session",
                "2026-01-01T00:00:00",
            )
        ],
    )
    tracking.set_head_session(parent, "controller-session")
    child = tracking.create_new_record(
        "child",
        "worktree/child",
        "/tmp/child",
        "example",
        "host",
        "windows",
        tmp_tracking_dir,
        parent_session="controller-session",
    )
    child_path = tmp_tracking_dir / "child.yaml"

    tracking.end_controller_relation(
        child,
        controller_session_id="controller-session",
        ended_at="2026-01-02T00:00:00",
        save=False,
    )
    tracking.save_record(child, child_path)
    ended = session_projection.read("controller-session")
    child_projection = next(
        relation for relation in ended["relations"]
        if relation["worktree_id"] == "child"
    )
    assert child_projection["role"] == "controller"
    assert child_projection["relation_state"] == "ended"
    assert child_projection["ended_at"] == "2026-01-02T00:00:00"
    assert child.resolved_head_session is None

    tracking.remove_controller_relation(
        child,
        controller_session_id="controller-session",
        save=False,
    )
    tracking.save_record(child, child_path)
    removed = session_projection.read("controller-session")
    assert {
        (relation["worktree_id"], relation["role"])
        for relation in removed["relations"]
    } == {("parent", "bound")}
    assert removed["relation_tombstones"] == [{
        "project": "example",
        "worktree_id": "child",
        "role": "controller",
        "relation_revision": child.controller_revision,
    }]
    reloaded = tracking.load_record(child_path)
    assert reloaded.controllers == []
    assert reloaded.controller_revision > 0
    assert reloaded.resolved_head_session is None
    row = cli._worktree_to_dict(reloaded)
    assert row["controller_revision"] == reloaded.controller_revision
    assert row["controllers"] == []


@pytest.mark.parametrize(
    ("controller_ref", "controller_session_id"),
    [
        ("host//child", None),
        ("host/example/child/extra", None),
        ("host/example/child#one", "two"),
        (None, "../session"),
    ],
)
def test_invalid_controller_identity_is_rejected(
    tmp_tracking_dir: Path,
    controller_ref: str | None,
    controller_session_id: str | None,
) -> None:
    record = _record(tmp_tracking_dir, "child")

    with pytest.raises(tracking.ControllerRelationError):
        tracking.set_controller_relation(
            record,
            controller_ref=controller_ref,
            controller_session_id=controller_session_id,
            save=False,
        )


def test_invalid_persisted_controller_counter_degrades_without_hiding_record(
    tmp_path: Path,
) -> None:
    path = tmp_path / "child.yaml"
    path.write_text(
        "worktree_id: child\n"
        "branch: worktree/child\n"
        "worktree_path: /tmp/child\n"
        "repo: example\n"
        "machine: host\n"
        "platform: windows\n"
        "started_at: t\n"
        "last_resumed_at: t\n"
        "resume_count: 0\n"
        "title: null\n"
        "status: active\n"
        "completed_at: null\n"
        "controller_revision: .inf\n"
        "controllers:\n"
        "- kind: session\n"
        "  source: explicit\n"
        "  controller_session_id: controller-session\n"
        "  state: active\n"
        "  relation_revision: 1\n"
        "  created_at: t\n"
        "sessions: []\n",
        encoding="utf-8",
    )

    record = tracking.load_record(path)
    assert record.worktree_id == "child"
    assert record.controller_revision == 1
    assert [
        relation.controller_session_id for relation in record.controllers
    ] == ["controller-session"]
    assert record.controller_metadata_opaque is True


def test_non_string_persisted_controller_identity_degrades_without_hiding_record(
    tmp_path: Path,
) -> None:
    path = tmp_path / "child.yaml"
    path.write_text(
        "worktree_id: child\n"
        "branch: worktree/child\n"
        "worktree_path: /tmp/child\n"
        "repo: example\n"
        "machine: host\n"
        "platform: windows\n"
        "started_at: t\n"
        "last_resumed_at: t\n"
        "resume_count: 0\n"
        "title: null\n"
        "status: active\n"
        "completed_at: null\n"
        "controller_revision: 1\n"
        "controllers:\n"
        "- kind: worktree\n"
        "  source: explicit\n"
        "  controller_ref: [parent]\n"
        "  state: active\n"
        "  relation_revision: 1\n"
        "  created_at: t\n"
        "sessions: []\n",
        encoding="utf-8",
    )

    record = tracking.load_record(path)
    assert record.worktree_id == "child"
    assert record.controller_revision == 1
    assert record.controllers == []
    assert record.controller_metadata_opaque is True

    record.summary = "unrelated"
    tracking.save_record(record, path)
    saved = path.read_text(encoding="utf-8")
    assert "controller_ref:" in saved
    assert "- parent" in saved
    assert tracking.load_record(path).controller_metadata_opaque is True

    with pytest.raises(
        tracking.ControllerRelationError,
        match="explicit repair",
    ):
        tracking.set_controller_relation(
            record,
            controller_session_id="new-controller",
            save=False,
        )


def test_malformed_controller_preserves_valid_relations_and_raw_authority(
    tmp_path: Path,
) -> None:
    path = tmp_path / "child.yaml"
    path.write_text(
        "worktree_id: child\n"
        "branch: worktree/child\n"
        "worktree_path: /tmp/child\n"
        "repo: example\n"
        "machine: host\n"
        "platform: windows\n"
        "started_at: t\n"
        "last_resumed_at: t\n"
        "resume_count: 0\n"
        "title: null\n"
        "status: active\n"
        "completed_at: null\n"
        "controller_revision: 2\n"
        "controllers:\n"
        "- kind: session\n"
        "  source: explicit\n"
        "  controller_session_id: valid-session\n"
        "  state: active\n"
        "  relation_revision: 1\n"
        "  created_at: t\n"
        "- kind: worktree\n"
        "  source: explicit\n"
        "  controller_ref: [invalid]\n"
        "  state: active\n"
        "  relation_revision: 2\n"
        "  created_at: t\n"
        "sessions: []\n",
        encoding="utf-8",
    )

    record = tracking.load_record(path)

    assert record.controller_metadata_opaque is True
    assert [
        relation.controller_session_id for relation in record.controllers
    ] == ["valid-session"]

    record.title = "Preserved"
    tracking.save_record(record, path)
    saved = path.read_text(encoding="utf-8")
    assert "controller_session_id: valid-session" in saved
    assert "controller_ref:" in saved
    assert "- invalid" in saved


@pytest.mark.parametrize(
    "controllers_yaml",
    [
        "controllers: null\n",
        "controllers:\n  future: shape\n",
        (
            "controllers:\n"
            "- kind: future-kind\n"
            "  source: explicit\n"
            "  controller_session_id: future-session\n"
            "  state: active\n"
            "  relation_revision: 2\n"
            "  created_at: t\n"
        ),
        (
            "controllers:\n"
            "- kind: session\n"
            "  source: explicit\n"
            "  controller_session_id: valid-session\n"
            "  state: active\n"
            "  relation_revision: 1\n"
            "  created_at: t\n"
            "  future_authority: retained\n"
        ),
    ],
)
def test_unknown_controller_schema_is_opaque_and_preserved(
    tmp_path: Path,
    controllers_yaml: str,
) -> None:
    path = tmp_path / "child.yaml"
    path.write_text(
        "worktree_id: child\n"
        "branch: worktree/child\n"
        "worktree_path: /tmp/child\n"
        "repo: example\n"
        "machine: host\n"
        "platform: windows\n"
        "started_at: t\n"
        "last_resumed_at: t\n"
        "resume_count: 0\n"
        "title: null\n"
        "status: active\n"
        "completed_at: null\n"
        "controller_revision: 2\n"
        + controllers_yaml
        + "sessions: []\n",
        encoding="utf-8",
    )

    record = tracking.load_record(path)
    assert record.controller_metadata_opaque is True
    record.summary = "ordinary save"
    tracking.save_record(record, path)
    saved = path.read_text(encoding="utf-8")
    assert (
        "controllers: null" in saved
        or "future: shape" in saved
        or "future-" in saved
        or "future_authority" in saved
    )

    with pytest.raises(
        tracking.ControllerRelationError,
        match="explicit repair",
    ):
        tracking.set_controller_relation(
            record,
            controller_session_id="new-controller",
            save=False,
        )


def test_controller_history_is_bounded_and_active_relations_are_protected(
    tmp_tracking_dir: Path,
) -> None:
    record = _record(tmp_tracking_dir, "child")
    for index in range(tracking._MAX_CONTROLLER_RELATIONS):
        tracking.set_controller_relation(
            record,
            controller_session_id=f"ended-{index}",
            save=False,
        )
        tracking.end_controller_relation(
            record,
            controller_session_id=f"ended-{index}",
            save=False,
        )

    tracking.set_controller_relation(
        record,
        controller_session_id="current-controller",
        save=False,
    )

    assert len(record.controllers) == tracking._MAX_CONTROLLER_RELATIONS
    assert record.controller_for_session("current-controller") is not None
    assert record.controller_for_session("ended-0") is None

    active = _record(tmp_tracking_dir, "active-child")
    for index in range(tracking._MAX_CONTROLLER_RELATIONS):
        tracking.set_controller_relation(
            active,
            controller_session_id=f"active-{index}",
            save=False,
        )
    with pytest.raises(tracking.ControllerRelationError):
        tracking.set_controller_relation(
            active,
            controller_session_id="active-overflow",
            save=False,
        )


def test_stale_record_writer_cannot_roll_back_controller_revision(
    tmp_tracking_dir: Path,
) -> None:
    path = tmp_tracking_dir / "child.yaml"
    _record(tmp_tracking_dir, "child")
    stale = tracking.load_record(path)
    current = tracking.load_record(path)
    tracking.set_controller_relation(
        current,
        controller_session_id="controller-session",
        save=False,
    )
    tracking.save_record(current, path)

    stale.summary = "unrelated update"
    tracking.save_record(stale, path)

    loaded = tracking.load_record(path)
    assert loaded.summary == "unrelated update"
    assert loaded.controller_revision == current.controller_revision
    assert (
        loaded.controller_for_session("controller-session") is not None
    )


def test_stale_controller_mutations_reload_before_allocating_revision(
    tmp_tracking_dir: Path,
) -> None:
    path = tmp_tracking_dir / "child.yaml"
    _record(tmp_tracking_dir, "child")
    first = tracking.load_record(path)
    second = tracking.load_record(path)

    tracking.set_controller_relation(
        first,
        controller_session_id="controller-one",
        path=path,
    )
    tracking.set_controller_relation(
        second,
        controller_session_id="controller-two",
        path=path,
    )

    loaded = tracking.load_record(path)
    assert loaded.controller_revision == 2
    assert {
        relation.controller_session_id for relation in loaded.controllers
    } == {"controller-one", "controller-two"}


def test_controller_mutation_preserves_unrelated_newer_record_state(
    tmp_tracking_dir: Path,
) -> None:
    path = tmp_tracking_dir / "child.yaml"
    _record(tmp_tracking_dir, "child")
    stale = tracking.load_record(path)
    current = tracking.load_record(path)
    current.status = "complete"
    current.title = "Finished"
    tracking.save_record(current, path)

    tracking.set_controller_relation(
        stale,
        controller_session_id="controller-session",
        path=path,
    )

    loaded = tracking.load_record(path)
    assert loaded.status == "complete"
    assert loaded.title == "Finished"
    assert (
        loaded.controller_for_session("controller-session") is not None
    )


def test_projection_tombstone_rejects_delayed_controller_upsert(
    tmp_path: Path,
    tmp_tracking_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    monkeypatch_config,
) -> None:
    _session_root(tmp_path, monkeypatch, "controller-session")
    child = tracking.create_new_record(
        "child",
        "worktree/child",
        "/tmp/child",
        "example",
        "host",
        "windows",
        tmp_tracking_dir,
        parent_session="controller-session",
    )
    stale = tracking.load_record(tmp_tracking_dir / "child.yaml")
    tracking.remove_controller_relation(
        child,
        controller_session_id="controller-session",
        save=False,
    )
    tracking.save_record(child, tmp_tracking_dir / "child.yaml")

    assert (
        session_projection.sync_controller(stale, "controller-session")
        == "current"
    )
    projection = session_projection.read("controller-session")
    assert projection["relations"] == []
    assert projection["relation_tombstones"][0]["relation_revision"] == (
        child.controller_revision
    )


def test_removal_preserves_newer_existing_tombstone() -> None:
    key = ("example", "child", "controller")
    projection = {
        "version": 1,
        "session_id": "controller-session",
        "relations": [{
            "project": key[0],
            "worktree_id": key[1],
            "role": key[2],
            "relation_revision": 3,
        }],
        "relation_tombstones": [{
            "project": key[0],
            "worktree_id": key[1],
            "role": key[2],
            "relation_revision": 7,
        }],
        "overflow": False,
        "omitted_relations": 0,
    }

    removed = session_projection._remove_relation(projection, key, 4)

    assert removed["relations"] == []
    assert removed["relation_tombstones"] == [{
        "project": key[0],
        "worktree_id": key[1],
        "role": key[2],
        "relation_revision": 7,
    }]


def test_new_tombstone_survives_cap_with_record_local_revision() -> None:
    projection = {
        "version": 1,
        "session_id": "controller-session",
        "relations": [],
        "relation_tombstones": [
            {
                "project": "example",
                "worktree_id": f"old-{index}",
                "role": "controller",
                "relation_revision": 100,
            }
            for index in range(session_projection.MAX_RELATION_TOMBSTONES)
        ],
        "overflow": False,
        "omitted_relations": 0,
    }
    key = ("example", "new-child", "controller")

    removed = session_projection._remove_relation(projection, key, 2)

    assert len(removed["relation_tombstones"]) == (
        session_projection.MAX_RELATION_TOMBSTONES
    )
    assert removed["relation_tombstones"][-1] == {
        "project": "example",
        "worktree_id": "new-child",
        "role": "controller",
        "relation_revision": 2,
    }
    stale = {
        "project": "example",
        "worktree_id": "new-child",
        "role": "controller",
        "relation_revision": 1,
    }
    assert session_projection._merge_relation(removed, stale) == removed


def test_conflicting_controller_ref_and_session_are_rejected(
    tmp_tracking_dir: Path,
) -> None:
    record = _record(tmp_tracking_dir, "child")
    tracking.set_controller_relation(
        record,
        controller_ref="host/example/controller-a#session-a",
        save=False,
    )
    tracking.set_controller_relation(
        record,
        controller_ref="host/example/controller-b#session-b",
        save=False,
    )
    before = list(record.controllers)

    with pytest.raises(
        tracking.ControllerRelationError,
        match="different relations",
    ):
        tracking.set_controller_relation(
            record,
            controller_ref="host/example/controller-a#session-b",
            save=False,
        )

    assert record.controllers == before
    assert {
        relation.controller_session_id for relation in record.controllers
    } == {"session-a", "session-b"}


def test_full_ref_selector_requires_its_embedded_session(
    tmp_tracking_dir: Path,
) -> None:
    record = _record(tmp_tracking_dir, "child")
    tracking.set_controller_relation(
        record,
        controller_ref="host/example/parent#old-session",
        save=False,
    )
    tracking.set_controller_relation(
        record,
        controller_ref="host/example/parent#new-session",
        save=False,
    )

    with pytest.raises(
        tracking.ControllerRelationError,
        match="not found",
    ):
        tracking.end_controller_relation(
            record,
            controller_ref="host/example/parent#old-session",
            save=False,
        )

    relation = record.controller_for_session("new-session")
    assert relation is not None
    assert relation.state == "active"


def test_controller_metadata_is_additive_to_json_surfaces(
    tmp_tracking_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    monkeypatch_config,
) -> None:
    record = tracking.create_new_record(
        "child",
        "worktree/child",
        "/tmp/child",
        "example",
        "host",
        "windows",
        tmp_tracking_dir,
        parent_session="controller-session",
    )
    expected = [
        tracking.controller_relation_to_dict(record.controllers[0])
    ]

    row = cli._worktree_to_dict(record)
    assert row["controllers"] == expected
    assert "last_session_id" not in row

    captured: dict = {}
    monkeypatch.setattr(cli, "_all_tracking_dirs", lambda: [tmp_tracking_dir])
    monkeypatch.setattr(
        cli, "_json_output", lambda data: captured.clear() or captured.update(data)
    )
    assert cli.cmd_head_session(
        argparse.Namespace(worktree_id="child", json=True)
    ) == 0
    assert captured["controllers"] == expected
    assert captured["head_session"] is None
    assert captured["active"] is False
    assert captured["occupied"] is False

    monkeypatch.setattr(
        sessions, "list_worktree_sessions", lambda _record: []
    )
    assert cli.cmd_list_sessions(
        argparse.Namespace(
            worktree_id="child",
            all_projects=False,
            json=True,
        )
    ) == 0
    assert captured["controllers"] == expected
    assert captured["head_session"] is None
    assert captured["sessions"] == []


def test_picker_passes_controllers_without_deriving_active_or_resume() -> None:
    controllers = [{
        "kind": "session",
        "source": "explicit",
        "controller_ref": None,
        "controller_session_id": "controller-session",
        "state": "active",
        "relation_revision": 1,
        "created_at": "2026-01-01T00:00:00",
        "ended_at": None,
    }]
    row = derive.norm(
        {
            "id": "child",
            "branch": "worktree/child",
            "path": "/tmp/child",
            "repo": "example",
            "status": "complete",
            "state": "clean",
            "started_at": "2026-01-01T00:00:00",
            "controllers": controllers,
        },
        "host",
        "windows",
    )

    assert row["controllers"] == controllers
    assert row["active"] is False
    assert row["last_session_id"] is None
    assert row["state"] != "ACTIVE"
