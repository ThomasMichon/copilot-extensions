"""Admission-only constraints survive the finite Windows admission callback."""

from __future__ import annotations

import sys

import pytest

from agent_worktrees import config as cfg, launch_seed_native as native, launch_seed_state as seeds
from agent_worktrees import launch_seed_exec as legacy, session_liveness, tracking
from test_launch_seed_native import record as record


@pytest.mark.parametrize("change", ["none", "seed", "resume-live"])
def test_admit_cli_rechecks_preserved_seed_and_native_resume(record, monkeypatch, capsys, change):
    seed = seeds.stage(record, kind="new", text="preserved")
    monkeypatch.setattr(legacy.sessions, "has_mux_session", lambda *_: False)
    monkeypatch.setattr(session_liveness, "session_liveness", lambda *_: "dead")
    current = tracking.load_record(record)
    current.head_session = "native-original"
    current.sessions = [tracking.SessionEntry("native-original", current.started_at)]
    tracking.save_record(current, record)
    prepared = native.prepare(
        record, ["copilot", "--resume=native-original"], seed_id=None,
        admission_only=True, preserve_seed_id=seed.seed_id,
        native_resume_session="native-original",
    )
    if change == "seed":
        seeds.stage(record, kind="new", text="replacement")
    elif change == "resume-live":
        monkeypatch.setattr(session_liveness, "session_liveness", lambda *_: "alive")
    before = seeds.peek(record)
    monkeypatch.setattr(native, "require_current_runtime", lambda root: None)
    monkeypatch.setattr(sys, "argv", [
        "launch_seed_native", "--contract-version", "1", "--root", str(cfg.install_dir()),
        "--record", str(record), "--phase", "admit",
        "--launch-attempt-id", prepared["attempt_id"],
        "--launch-resolution-id", prepared["resolution_id"],
        "--operation-id", prepared["operation_id"], "--admission-only",
        "--preserve-seed-id", seed.seed_id, "--native-resume-session", "native-original",
    ])
    assert native.main() == (0 if change == "none" else 3)
    output = capsys.readouterr()
    assert ('"recorded": true' in output.out) is (change == "none")
    assert ("Startup admission refused: ValueError: " in output.err) is (change != "none")
    assert seeds.peek(record) == before
    assert before.handoff_id is None
