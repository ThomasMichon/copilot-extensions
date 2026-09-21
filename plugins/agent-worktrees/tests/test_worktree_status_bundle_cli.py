"""Tests for `cmd_worktree_status_bundle`
(agent-worktrees-external-status-accelerator effort, Phase 4 -- the
in-process reference consumer).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agent_worktrees import __main__ as cli
from agent_worktrees import session_tracking_cli
from agent_worktrees import tracking
from agent_worktrees import worktree_status_daemon


def _record() -> tracking.WorktreeRecord:
    return tracking.WorktreeRecord(
        worktree_id="wt1", branch="worktree/wt1", worktree_path="/tmp/wt1",
        repo="ext", machine="m1", platform="wsl",
        started_at="2026-06-01T10:00:00", last_resumed_at="2026-06-01T10:00:00",
        resume_count=0, title=None, status="active", completed_at=None,
        sessions=None,
    )


def _wire_record_resolution(monkeypatch, tmp_path: Path, *, project="proj"):
    record_path = tmp_path / "wt1.yaml"
    record = _record()
    monkeypatch.setattr(
        session_tracking_cli, "_find_tracking_file", lambda _worktree_id: record_path
    )
    monkeypatch.setattr(
        session_tracking_cli, "_project_for_tracking_file", lambda _path: project
    )
    monkeypatch.setattr(tracking, "load_record", lambda _path: record)
    return record


def test_worktree_not_found_reports_json_error(monkeypatch):
    errors = []
    monkeypatch.setattr(cli, "_json_error", lambda msg: errors.append(msg) or 1)
    monkeypatch.setattr(
        session_tracking_cli, "_find_tracking_file", lambda _worktree_id: None
    )

    rc = cli.cmd_worktree_status_bundle(
        argparse.Namespace(worktree_id="missing", force_refresh=False, json=True)
    )
    assert rc == 1
    assert errors and "missing" in errors[0]


def test_unresolvable_project_reports_json_error(monkeypatch, tmp_path):
    errors = []
    monkeypatch.setattr(cli, "_json_error", lambda msg: errors.append(msg) or 1)
    monkeypatch.setattr(
        session_tracking_cli, "_find_tracking_file", lambda _worktree_id: tmp_path / "wt1.yaml"
    )
    monkeypatch.setattr(
        session_tracking_cli, "_project_for_tracking_file", lambda _path: None
    )

    rc = cli.cmd_worktree_status_bundle(
        argparse.Namespace(worktree_id="wt1", force_refresh=False, json=True)
    )
    assert rc == 1
    assert errors


def test_uses_daemon_when_reachable(monkeypatch, tmp_path):
    _wire_record_resolution(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_monitor_lock_path", lambda: tmp_path / "status-monitor.lock")
    monkeypatch.setattr(cli, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(cli, "_ensure_status_monitor", lambda: True)

    captured_payload = {}

    def fake_status_with_boot(*, read_lock_data, ensure_monitor, key, payload, fallback):
        captured_payload.update(payload)
        return {"worktree_id": payload["worktree_id"], "from": "daemon"}

    monkeypatch.setattr(worktree_status_daemon, "status_with_boot", fake_status_with_boot)

    outputs = []
    monkeypatch.setattr(cli, "_json_output", outputs.append)

    rc = cli.cmd_worktree_status_bundle(
        argparse.Namespace(worktree_id="wt1", force_refresh=False, json=True)
    )
    assert rc == 0
    assert outputs == [{"worktree_id": "wt1", "from": "daemon"}]
    assert captured_payload == {"project": "proj", "worktree_id": "wt1", "force": False}


def test_force_refresh_flag_propagates_to_payload(monkeypatch, tmp_path):
    _wire_record_resolution(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_monitor_lock_path", lambda: tmp_path / "status-monitor.lock")
    monkeypatch.setattr(cli, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(cli, "_ensure_status_monitor", lambda: True)

    captured_payload = {}

    def fake_status_with_boot(*, read_lock_data, ensure_monitor, key, payload, fallback):
        captured_payload.update(payload)
        return {"ok": True}

    monkeypatch.setattr(worktree_status_daemon, "status_with_boot", fake_status_with_boot)
    monkeypatch.setattr(cli, "_json_output", lambda _v: None)

    cli.cmd_worktree_status_bundle(
        argparse.Namespace(worktree_id="wt1", force_refresh=True, json=True)
    )
    assert captured_payload["force"] is True


def test_falls_back_to_direct_compute_when_daemon_unreachable(monkeypatch, tmp_path):
    _wire_record_resolution(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_monitor_lock_path", lambda: tmp_path / "status-monitor.lock")
    monkeypatch.setattr(cli, "_status_monitor_enabled", lambda: False)

    def fake_status_with_boot(*, read_lock_data, ensure_monitor, key, payload, fallback):
        assert ensure_monitor is None  # monitor opted out -> dial-only, no boot
        return fallback()

    monkeypatch.setattr(worktree_status_daemon, "status_with_boot", fake_status_with_boot)
    monkeypatch.setattr(
        cli, "_worktree_status_compute", lambda project, worktree_id: {"from": "direct-compute"}
    )
    outputs = []
    monkeypatch.setattr(cli, "_json_output", outputs.append)

    rc = cli.cmd_worktree_status_bundle(
        argparse.Namespace(worktree_id="wt1", force_refresh=False, json=True)
    )
    assert rc == 0
    assert outputs == [{"from": "direct-compute"}]


def test_direct_fallback_rejects_a_tampered_record_worktree_id(monkeypatch, tmp_path):
    """Copilot review finding: `record.worktree_id` comes from the YAML's
    own content, not necessarily `args.worktree_id` -- a malformed/tampered
    record could carry a traversal token past the file-path resolution the
    CLI already did, so the direct-compute fallback must apply the same
    `validated_refresh` guard the daemon request path and sweep already
    use."""
    record_path = tmp_path / "wt1.yaml"
    tampered_record = tracking.WorktreeRecord(
        worktree_id="../evil", branch="worktree/wt1", worktree_path="/tmp/wt1",
        repo="ext", machine="m1", platform="wsl",
        started_at="2026-06-01T10:00:00", last_resumed_at="2026-06-01T10:00:00",
        resume_count=0, title=None, status="active", completed_at=None,
        sessions=None,
    )
    monkeypatch.setattr(
        session_tracking_cli, "_find_tracking_file", lambda _worktree_id: record_path
    )
    monkeypatch.setattr(
        session_tracking_cli, "_project_for_tracking_file", lambda _path: "proj"
    )
    monkeypatch.setattr(tracking, "load_record", lambda _path: tampered_record)
    monkeypatch.setattr(cli, "_monitor_lock_path", lambda: tmp_path / "status-monitor.lock")
    monkeypatch.setattr(cli, "_status_monitor_enabled", lambda: False)

    assembled = []
    monkeypatch.setattr(
        cli, "_worktree_status_compute",
        lambda project, worktree_id: assembled.append(1) or {"from": "direct-compute"},
    )

    def fake_status_with_boot(*, read_lock_data, ensure_monitor, key, payload, fallback):
        return fallback()

    monkeypatch.setattr(worktree_status_daemon, "status_with_boot", fake_status_with_boot)

    errors = []
    monkeypatch.setattr(cli, "_json_error", lambda msg: errors.append(msg) or 1)
    outputs = []
    monkeypatch.setattr(cli, "_json_output", outputs.append)

    raised = False
    try:
        cli.cmd_worktree_status_bundle(
            argparse.Namespace(worktree_id="wt1", force_refresh=False, json=True)
        )
    except ValueError:
        raised = True
    # The command itself doesn't catch this ValueError (that's the CLI
    # dispatch layer's job elsewhere) -- what matters here is the
    # fact-assembly function was never reached with the tampered id.
    assert raised
    assert not assembled

