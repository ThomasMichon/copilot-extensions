"""Tests for conservative resident pane-drift repair."""

from __future__ import annotations

from agent_worktrees import config as cfg
from agent_worktrees import pane_reaper
from agent_worktrees import tracking


def _record() -> tracking.WorktreeRecord:
    return tracking.WorktreeRecord(
        worktree_id="wt-a",
        branch="worktree/wt-a",
        worktree_path="/w/a",
        repo="test",
        machine="test",
        platform="windows",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[
            tracking.SessionEntry(
                "current", "2026-06-02T10:00:00", pane_id="%1"),
            tracking.SessionEntry(
                "old", "2026-06-01T10:00:00",
                state="handed-off", pane_id="%2"),
            tracking.SessionEntry(
                "old-live", "2026-06-01T11:00:00",
                state="concluded", pane_id="%3"),
        ],
        head_session="current",
    )


def test_classifier_requires_positive_orphan_proof():
    panes = [
        {"pane": "%1", "dead": False, "pid": 1, "command": "node"},
        {"pane": "%2", "dead": False, "pid": 2, "command": "node"},
        {"pane": "%3", "dead": False, "pid": 3, "command": "shell"},
        {"pane": "%4", "dead": False, "pid": 4, "command": "conhost"},
        {"pane": "%5", "dead": True, "pid": 5, "command": "shell"},
    ]

    result = pane_reaper.classify_panes(
        _record(),
        panes,
        active_pane="%1",
        session_is_live=lambda sid: sid == "old-live",
    )

    assert {item["pane"] for item in result["repairable"]} == {"%2", "%5"}
    assert {item["pane"] for item in result["ambiguous"]} == {"%3", "%4"}
    assert {
        item["pane"]: item["reason"] for item in result["ambiguous"]
    }["%4"] == "unproven-extra-pane"
    assert {item["pane"] for item in result["protected"]} == {"%1"}


def test_active_pane_veto_wins_over_concluded_record():
    result = pane_reaper.classify_panes(
        _record(),
        [{"pane": "%2", "dead": False, "pid": 2, "command": "node"}],
        active_pane="%2",
        session_is_live=lambda sid: False,
    )
    assert result["repairable"] == []
    assert result["ambiguous"] == []


def test_unresolved_active_pane_vetoes_live_concluded_candidate():
    result = pane_reaper.classify_panes(
        _record(),
        [{"pane": "%2", "dead": False, "pid": 2, "command": "node"}],
        active_pane=None,
        session_is_live=lambda sid: False,
    )
    assert result["repairable"] == []
    assert result["ambiguous"][0]["reason"] == "active-pane-unresolved-veto"


def test_missing_session_state_is_not_orphan_proof():
    result = pane_reaper.classify_panes(
        _record(),
        [{"pane": "%2", "dead": False, "pid": 2, "command": "node"}],
        active_pane="%1",
        session_is_live=lambda sid: None,
    )
    assert result["repairable"] == []
    assert result["ambiguous"][0]["reason"] == (
        "concluded-session-state-missing-veto")


def test_resident_step_repairs_one_proven_pane_and_reports_ambiguous(
    tmp_path, monkeypatch
):
    tracking_dir = tmp_path / "tracking"
    tracking_dir.mkdir()
    rec = _record()
    rec.worktree_path = str(tmp_path / "wt")
    tracking.save_record(rec, tracking_dir / "wt-a.yaml")
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tracking_dir)
    monkeypatch.setattr(
        pane_reaper,
        "_list_panes",
        lambda mux, session: [
            {"pane": "%2", "dead": False, "pid": 2, "command": "node"},
            {"pane": "%9", "dead": False, "pid": 9, "command": "conhost"},
        ],
    )
    monkeypatch.setattr(
        pane_reaper.sessions, "mux_active_pane", lambda *a, **k: "%1")
    monkeypatch.setattr(pane_reaper, "_report_dir", lambda: tmp_path / "reports")
    retired: list[str] = []
    reconciler = pane_reaper.ResidentPaneReconciler(
        activate_project=lambda *a, **k: None,
        retire_pane=(
            lambda pane, **kw: retired.append(pane)
            or {"ok": True, "gone": True, "method": "hard"}),
        session_is_live=lambda sid: False,
        pane_has_live_copilot=lambda pane: False,
        client_count=lambda mux, session: 1,
    )
    reconciler.observe("wt-wt-a", rec.worktree_path)

    report = reconciler.step("psmux")

    assert retired == ["%2"]
    assert [item["pane"] for item in report["ambiguous"]] == ["%9"]
    assert report["action"]["gone"] is True
    assert (tmp_path / "reports" / "wt-wt-a.json").is_file()


def test_ambiguous_pane_is_never_repaired(tmp_path, monkeypatch):
    tracking_dir = tmp_path / "tracking"
    tracking_dir.mkdir()
    rec = _record()
    rec.sessions = [
        tracking.SessionEntry(
            "current", "2026-06-02T10:00:00", pane_id="%1")]
    tracking.save_record(rec, tracking_dir / "wt-a.yaml")
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tracking_dir)
    monkeypatch.setattr(
        pane_reaper,
        "_list_panes",
        lambda mux, session: [
            {"pane": "%9", "dead": False, "pid": 9, "command": "node"}],
    )
    monkeypatch.setattr(
        pane_reaper.sessions, "mux_active_pane", lambda *a, **k: "%1")
    retired: list[str] = []
    reconciler = pane_reaper.ResidentPaneReconciler(
        activate_project=lambda *a, **k: None,
        retire_pane=lambda pane, **kw: retired.append(pane) or {},
        pane_has_live_copilot=lambda pane: False,
        client_count=lambda mux, session: 1,
    )
    reconciler.observe("wt-wt-a", rec.worktree_path)

    report = reconciler.step("psmux")

    assert retired == []
    assert report["repairable"] == []
    assert report["ambiguous"][0]["pane"] == "%9"


def test_live_copilot_process_tree_vetoes_repair(tmp_path, monkeypatch):
    tracking_dir = tmp_path / "tracking"
    tracking_dir.mkdir()
    rec = _record()
    tracking.save_record(rec, tracking_dir / "wt-a.yaml")
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tracking_dir)
    monkeypatch.setattr(
        pane_reaper,
        "_list_panes",
        lambda mux, session: [
            {"pane": "%1", "dead": False, "pid": 1, "command": "node"},
            {"pane": "%2", "dead": False, "pid": 2, "command": "node"},
        ],
    )
    monkeypatch.setattr(
        pane_reaper.sessions, "mux_active_pane", lambda *a, **k: "%1")
    retired: list[str] = []
    reconciler = pane_reaper.ResidentPaneReconciler(
        activate_project=lambda *a, **k: None,
        retire_pane=lambda pane, **kw: retired.append(pane) or {},
        session_is_live=lambda sid: False,
        pane_has_live_copilot=lambda pane: pane["pane"] == "%2",
        client_count=lambda mux, session: 1,
    )
    reconciler.observe("wt-wt-a", rec.worktree_path)

    report = reconciler.step("psmux")

    assert retired == []
    assert report["repairable"] == []
    assert report["ambiguous"][0]["reason"] == "pane-live-copilot-veto"


def test_file_brake_disables_repair(tmp_path, monkeypatch):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "DISABLED").write_text("", encoding="utf-8")
    monkeypatch.setattr(pane_reaper, "_report_dir", lambda: report_dir)
    monkeypatch.delenv(pane_reaper._REPAIR_ENV, raising=False)
    assert pane_reaper.repair_enabled() is False


def test_missing_pane_root_with_live_descendant_vetoes(monkeypatch):
    from agent_worktrees import reclaim

    monkeypatch.setattr(
        reclaim,
        "build_process_table",
        lambda: {22: {"ppid": 11, "name": "copilot.exe"}},
    )
    monkeypatch.setattr(
        pane_reaper.sessions, "_is_copilot_process", lambda pid: pid == 22)
    pane = {"pane": "%2", "dead": False, "pid": 11, "command": "shell"}
    assert pane_reaper._pane_has_live_copilot(pane) is True
