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


def test_rejects_a_tampered_record_worktree_id_before_any_substitution(monkeypatch, tmp_path):
    """Copilot review finding (originally): `record.worktree_id` comes from
    the YAML's own content, not necessarily `args.worktree_id` -- a
    malformed/tampered record could carry a traversal token past the
    file-path resolution the CLI already did.

    Fixed twice: round 5/6 wrapped the direct-compute fallback with
    `validated_refresh`, but a later review caught that this call site
    substitutes `worktree_id = record.worktree_id` *before* calling
    `_worktree_status_compute` -- making that function's own
    ``record.worktree_id != worktree_id`` mismatch guard a tautology (both
    sides are the same substituted value). The real fix is to validate the
    loaded record's identity against the *requested filename*
    (`yaml_path.stem`) in this command itself, before any substitution, and
    reject with a JSON error -- consistent with this function's other
    early-exit checks (not a raised exception from deep in the fallback)."""
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

    rc = cli.cmd_worktree_status_bundle(
        argparse.Namespace(worktree_id="wt1", force_refresh=False, json=True)
    )
    assert rc == 1
    assert errors  # a JSON error, not a raised exception
    assert not outputs
    # The fact-assembly function was never reached with the tampered id.
    assert not assembled


def test_rejects_a_record_declaring_a_different_real_worktrees_identity(
    monkeypatch, tmp_path
):
    """Copilot review finding: the tampered-id test above only exercises an
    *unsafe* substituted id (one `_worktree_status_compute`'s own path-
    traversal guard would reject anyway). It does not prove the bypass this
    finding actually describes: `wt1.yaml` declaring the *safe, real*
    identity `wt2` (with `wt2.yaml` also existing) would previously make
    this call site substitute `worktree_id = "wt2"` and call
    `_worktree_status_compute(project, "wt2")` -- serving worktree `wt2`'s
    facts for a request that asked about `wt1`, with no guard anywhere ever
    catching it. Must be rejected before any substitution."""
    record_path = tmp_path / "wt1.yaml"
    mismatched_record = tracking.WorktreeRecord(
        worktree_id="wt2", branch="worktree/wt2", worktree_path="/tmp/wt2",
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
    monkeypatch.setattr(tracking, "load_record", lambda _path: mismatched_record)
    monkeypatch.setattr(cli, "_monitor_lock_path", lambda: tmp_path / "status-monitor.lock")
    monkeypatch.setattr(cli, "_status_monitor_enabled", lambda: False)

    assembled = []
    monkeypatch.setattr(
        cli, "_worktree_status_compute",
        lambda project, worktree_id: assembled.append(worktree_id) or {"from": "direct-compute"},
    )

    def fake_status_with_boot(*, read_lock_data, ensure_monitor, key, payload, fallback):
        return fallback()

    monkeypatch.setattr(worktree_status_daemon, "status_with_boot", fake_status_with_boot)

    errors = []
    monkeypatch.setattr(cli, "_json_error", lambda msg: errors.append(msg) or 1)
    outputs = []
    monkeypatch.setattr(cli, "_json_output", outputs.append)

    rc = cli.cmd_worktree_status_bundle(
        argparse.Namespace(worktree_id="wt1", force_refresh=False, json=True)
    )
    assert rc == 1
    assert errors
    assert not outputs
    # Never called with "wt2" (nor "wt1") -- the mismatch is caught first.
    assert not assembled

