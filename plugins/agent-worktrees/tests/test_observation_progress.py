"""Cooperative progress and inventory capture stay inside their owner scope."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_worktrees import __main__ as core
from agent_worktrees import config as cfg
from agent_worktrees import delegate_cli, git_ops, reclaim, sessions, status_monitor_discovery
from agent_worktrees import tracking, worktrees_producer as producer
from agent_worktrees.observation_progress import (
    inventory_scope, observation_boundary, progress_scope,
)
from agent_worktrees.worktrees_runtime import WorktreesRuntime


class UnloadedRecord(tracking.WorktreeRecord):
    _loaded_record_subject: tuple[object, ...] | None = None


def test_roster_progress_reaches_classification_and_serialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    records = [
        UnloadedRecord(
            worktree_id=name, branch="worktree/" + name, worktree_path=str(tmp_path / name),
            repo="project", machine="host", platform="windows", title=name,
            started_at="", last_resumed_at="", resume_count=0, status="active",
            completed_at=None, sessions=[],
        )
        for name in ("one", "two")
    ]
    for record in records:
        Path(record.worktree_path).mkdir()
    monkeypatch.setattr(cfg, "project_name", lambda: "project")
    monkeypatch.setattr(
        cfg, "load_config",
        lambda *a, **k: SimpleNamespace(default_repo=SimpleNamespace(
            anchor=str(tmp_path), remote="origin", default_branch="main",
        )),
    )
    monkeypatch.setattr(tracking, "list_records", lambda *a, **k: records)
    monkeypatch.setattr(sessions, "scan_sessions_fast", lambda *a: sessions.SessionContext())
    monkeypatch.setattr(sessions, "mux_status_many", lambda *a: {})
    monkeypatch.setattr(core, "_build_active_paths", lambda *a: set())
    monkeypatch.setattr(reclaim, "bare_orphan_worktree_ids", lambda: set())
    monkeypatch.setattr(reclaim, "live_bridge_worktrees", lambda: set())
    monkeypatch.setattr(delegate_cli, "annotate_delegate_graph", lambda *a, **k: None)

    def classify(path: str, *args: Any, **options: Any) -> git_ops.WorktreeStateInfo:
        events.append("classify:" + Path(path).name)
        return git_ops.WorktreeStateInfo(git_ops.WorktreeState.WIP)

    def serialize(record: tracking.WorktreeRecord, **options: Any) -> dict[str, Any]:
        events.append("serialize:" + record.worktree_id)
        return {"worktree_id": record.worktree_id, "title": record.title}

    monkeypatch.setattr(git_ops, "classify_worktree", classify)
    monkeypatch.setattr(core, "_worktree_to_dict", serialize)
    monkeypatch.setattr(
        producer, "materialize",
        lambda items, serialize, **options: [serialize(record) for record in items],
    )
    published: list[tuple[Any, Any]] = []
    runtime = WorktreesRuntime(tmp_path / "current.sqlite3", now=lambda: 100.0)
    monkeypatch.setattr(runtime, "publish", lambda *a, **k: published.append((a, k)))
    monkeypatch.setattr(runtime, "publication_failed", lambda *a: pytest.fail("publication failed"))
    try:
        assert producer.publish_active_project(
            runtime, on_progress=lambda: events.append("progress"),
        ) == 2
        assert events == [
            "progress", "classify:one", "progress", "classify:two",
            "progress", "serialize:one", "progress", "serialize:two",
        ]
        assert len(published) == 1
        observation_boundary()
        assert events[-1] == "serialize:two"
    finally:
        runtime.shutdown()


def test_collector_captures_the_completed_batch_only_for_its_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_remote_driver import mux_observation

    monkeypatch.setattr(status_monitor_discovery, "is_psmux", lambda value: True)
    responses: list[Any] = [{
        "wt-one": SimpleNamespace(status="present", clients=2, incarnation="$7:100"),
        "wt-two": SimpleNamespace(status="absent"),
    }, None, {}]
    monkeypatch.setattr(mux_observation, "list_psmux", lambda: responses.pop(0))
    with inventory_scope() as capture:
        assert status_monitor_discovery.list_sessions("psmux.exe") == {"wt-one": (2, "$7:100")}
        assert capture.completed == {"wt-one": (2, "$7:100")}
        with inventory_scope() as nested:
            assert status_monitor_discovery.list_sessions("psmux.exe") is None
            assert nested.completed is None
        assert capture.completed == {"wt-one": (2, "$7:100")}
    assert status_monitor_discovery.list_sessions("psmux.exe") == {}
    assert capture.completed == {"wt-one": (2, "$7:100")}


def test_progress_scope_restores_outer_callback_after_failure() -> None:
    events: list[str] = []
    with progress_scope(lambda: events.append("outer")):
        with pytest.raises(RuntimeError, match="observation failed"):
            with progress_scope(lambda: events.append("inner")):
                observation_boundary()
                raise RuntimeError("observation failed")
        observation_boundary()
    observation_boundary()
    assert events == ["inner", "outer"]
