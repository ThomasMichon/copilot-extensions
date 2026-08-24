"""Tests for the resident bounded session/mux reconciler."""

from __future__ import annotations

from pathlib import Path

from conftest import make_session_dir

from agent_worktrees import config as cfg
from agent_worktrees import installer
from agent_worktrees import session_catalog
from agent_worktrees import sessions
from agent_worktrees import tracking


def _record(
    wt_id: str,
    path: str,
    *,
    sessions_list=None,
    head: str | None = None,
) -> tracking.WorktreeRecord:
    return tracking.WorktreeRecord(
        worktree_id=wt_id,
        branch=f"worktree/{wt_id}",
        worktree_path=path,
        repo="test",
        machine="test",
        platform="windows",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=sessions_list,
        head_session=head,
    )


def _wire(
    tmp_path: Path,
    monkeypatch,
    records: list[tracking.WorktreeRecord],
) -> tuple[Path, Path]:
    tracking_dir = tmp_path / "tracking"
    state_dir = tmp_path / "sessions"
    tracking_dir.mkdir()
    state_dir.mkdir()
    for record in records:
        tracking.save_record(
            record, tracking_dir / f"{record.worktree_id}.yaml")

    monkeypatch.setattr(
        installer,
        "read_projects_registry",
        lambda: {"projects": {"project-a": {}}},
    )
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tracking_dir)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: state_dir)
    return tracking_dir, state_dir


def test_registers_missing_session_on_populated_record(tmp_path, monkeypatch):
    rec = _record(
        "wt-a",
        str(tmp_path / "wt-a"),
        sessions_list=[
            tracking.SessionEntry("known", "2026-06-01T10:00:00")],
        head="known",
    )
    tracking_dir, state_dir = _wire(tmp_path, monkeypatch, [rec])
    make_session_dir(
        state_dir,
        "missing",
        rec.worktree_path,
        updated_at="2026-06-02T10:00:00.000Z",
    )
    monkeypatch.setattr(session_catalog, "_session_live_pid", lambda entry: None)

    reconciler = session_catalog.ResidentSessionReconciler(
        record_budget=8, session_budget=8)
    report = reconciler.step()

    after = tracking.load_record(tracking_dir / "wt-a.yaml")
    assert [entry.session_id for entry in after.sessions or []] == [
        "known", "missing"]
    assert after.session_entry("missing").state == "active"
    assert after.session_entry("missing").ended_at is None
    assert report["registered"] == 1


def test_discovery_preserves_existing_derived_head_and_same_day_order(
    tmp_path, monkeypatch
):
    current = tracking.SessionEntry("current", "2026-06-01T10:00:00")
    rec = _record(
        "wt-head",
        str(tmp_path / "wt-head"),
        sessions_list=[current],
    )
    tracking_dir, state_dir = _wire(tmp_path, monkeypatch, [rec])
    make_session_dir(
        state_dir,
        "older",
        rec.worktree_path,
        updated_at="2026-06-01T09:00:00.000Z",
    )
    make_session_dir(
        state_dir,
        "newer-unasserted",
        rec.worktree_path,
        updated_at="2026-06-01T20:00:00.000Z",
    )
    monkeypatch.setattr(session_catalog, "_session_live_pid", lambda entry: None)

    reconciler = session_catalog.ResidentSessionReconciler(
        record_budget=8, session_budget=8)
    reconciler.step()

    after = tracking.load_record(tracking_dir / "wt-head.yaml")
    assert [entry.session_id for entry in after.sessions or []] == [
        "older", "current", "newer-unasserted"]
    assert after.head_session == "current"
    assert after.resolved_head_session == "current"


def test_empty_registry_derives_newest_across_same_catalog_cycle(
    tmp_path, monkeypatch
):
    rec = _record("wt-empty", str(tmp_path / "wt-empty"), sessions_list=[])
    tracking_dir, state_dir = _wire(tmp_path, monkeypatch, [rec])
    make_session_dir(
        state_dir,
        "older",
        rec.worktree_path,
        updated_at="2026-06-01T09:00:00.000Z",
    )
    make_session_dir(
        state_dir,
        "newer",
        rec.worktree_path,
        updated_at="2026-06-01T20:00:00.000Z",
    )
    monkeypatch.setattr(session_catalog, "_session_live_pid", lambda entry: None)

    reconciler = session_catalog.ResidentSessionReconciler(
        record_budget=8, session_budget=8)
    reconciler.step()

    after = tracking.load_record(tracking_dir / "wt-empty.yaml")
    assert after.head_session is None
    assert after.resolved_head_session == "newer"


def test_malformed_workspace_is_skipped_without_stopping_cycle(
    tmp_path, monkeypatch
):
    rec = _record("wt-a", str(tmp_path / "wt-a"), sessions_list=[])
    tracking_dir, state_dir = _wire(tmp_path, monkeypatch, [rec])
    broken = make_session_dir(state_dir, "broken", rec.worktree_path)
    (broken / "workspace.yaml").write_text("cwd: [unterminated", encoding="utf-8")

    reconciler = session_catalog.ResidentSessionReconciler(
        record_budget=8, session_budget=8)
    report = reconciler.step()

    assert report["scanned_sessions"] == 1
    after = tracking.load_record(tracking_dir / "wt-a.yaml")
    assert after.sessions == []


def test_session_cursor_respects_budget_and_completes_cycle(
    tmp_path, monkeypatch
):
    rec = _record("wt-a", str(tmp_path / "wt-a"), sessions_list=[])
    tracking_dir, state_dir = _wire(tmp_path, monkeypatch, [rec])
    for index in range(5):
        make_session_dir(
            state_dir,
            f"session-{index}",
            rec.worktree_path,
            updated_at=f"2026-06-0{index + 1}T10:00:00.000Z",
        )
    monkeypatch.setattr(session_catalog, "_session_live_pid", lambda entry: None)

    reconciler = session_catalog.ResidentSessionReconciler(
        record_budget=8, session_budget=2)
    reports = [reconciler.step() for _ in range(3)]

    assert [report["scanned_sessions"] for report in reports] == [2, 2, 1]
    assert reports[-1]["cycle_complete"] is True
    after = tracking.load_record(tracking_dir / "wt-a.yaml")
    assert len(after.sessions or []) == 5


def test_repairs_stale_stored_head_without_concluding_sessions(
    tmp_path, monkeypatch
):
    old = tracking.SessionEntry(
        "old", "2026-06-01T10:00:00", state="concluded")
    current = tracking.SessionEntry("current", "2026-06-02T10:00:00")
    rec = _record(
        "wt-head",
        str(tmp_path / "wt-head"),
        sessions_list=[old, current],
        head="old",
    )
    tracking_dir, _state_dir = _wire(tmp_path, monkeypatch, [rec])

    reconciler = session_catalog.ResidentSessionReconciler(
        record_budget=8, session_budget=8)
    report = reconciler.step()

    after = tracking.load_record(tracking_dir / "wt-head.yaml")
    assert after.head_session == "current"
    assert after.session_entry("old").state == "concluded"
    assert after.session_entry("current").state == "active"
    assert report["heads"] == 1


def test_mux_catalog_refreshes_hint_and_monitor_registry(
    tmp_path, monkeypatch
):
    rec = _record("wt-mux", str(tmp_path / "wt-mux"), sessions_list=[])
    tracking_dir, _state_dir = _wire(tmp_path, monkeypatch, [rec])
    registered: list[tuple[str, str | None]] = []
    reconciler = session_catalog.ResidentSessionReconciler(
        record_budget=8,
        session_budget=8,
        register_monitor_session=(
            lambda name, path: registered.append((name, path)) or True),
    )
    reconciler.observe_mux({"wt-wt-mux", "unmanaged-session"})

    report = reconciler.step()

    after = tracking.load_record(tracking_dir / "wt-mux.yaml")
    assert after.mux_live is True
    assert registered == [("wt-wt-mux", rec.worktree_path)]
    assert report["registered_mux"] == 1
    assert reconciler.has_live_worktree_mux is True


def test_stale_mux_observation_is_not_restamped(tmp_path, monkeypatch):
    rec = _record("wt-mux", str(tmp_path / "wt-mux"), sessions_list=[])
    tracking_dir, _state_dir = _wire(tmp_path, monkeypatch, [rec])
    now = {"value": 100.0}
    monkeypatch.setattr(
        session_catalog.time, "monotonic", lambda: now["value"])
    reconciler = session_catalog.ResidentSessionReconciler(
        record_budget=8, session_budget=8, mux_max_age=45)
    reconciler.observe_mux({"wt-wt-mux"})
    now["value"] = 200.0

    reconciler.step()

    after = tracking.load_record(tracking_dir / "wt-mux.yaml")
    assert after.mux_live is None


def test_completed_record_is_not_reactivated(tmp_path, monkeypatch):
    rec = _record("wt-done", str(tmp_path / "wt-done"), sessions_list=[])
    rec.status = "complete"
    tracking_dir, state_dir = _wire(tmp_path, monkeypatch, [rec])
    make_session_dir(state_dir, "late", rec.worktree_path)

    reconciler = session_catalog.ResidentSessionReconciler(
        record_budget=8, session_budget=8)
    reconciler.step()

    after = tracking.load_record(tracking_dir / "wt-done.yaml")
    assert after.sessions == []


def test_project_index_remains_complete_until_shadow_pass_finishes(
    tmp_path, monkeypatch
):
    rec = _record("wt-a", str(tmp_path / "wt-a"), sessions_list=[])
    _tracking_dir, _state_dir = _wire(tmp_path, monkeypatch, [rec])
    reconciler = session_catalog.ResidentSessionReconciler(
        record_budget=1, session_budget=1)
    reconciler._projects = ["project-a"]
    key = session_catalog._path_key(rec.worktree_path)
    existing = ("project-a", rec.worktree_id, Path("old.yaml"), rec.worktree_path)
    reconciler._paths[key] = existing
    reconciler._project_path_keys["project-a"] = {key}

    assert reconciler._open_next_project() is True
    assert reconciler._paths[key] == existing
    reconciler._close_record_iter()


def test_record_cursor_counts_non_yaml_entries_toward_budget(
    tmp_path, monkeypatch
):
    rec = _record("wt-a", str(tmp_path / "wt-a"), sessions_list=[])
    tracking_dir, _state_dir = _wire(tmp_path, monkeypatch, [rec])
    for index in range(5):
        (tracking_dir / f"noise-{index}.txt").write_text("x", encoding="utf-8")

    reconciler = session_catalog.ResidentSessionReconciler(
        record_budget=1, session_budget=1)
    reconciler.step()

    assert reconciler._record_iter is not None
