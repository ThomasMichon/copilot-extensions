"""Windows open-file ownership, without launching a native agent."""

import os
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from agent_worktrees import locks, native_lifecycle_source, native_source_context
from agent_worktrees import session_liveness, sessions
from agent_worktrees import windows_session_hold


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows native API")
SID = "00000000-0000-4000-8000-000000000001"


def test_real_open_hold_matches_birth_and_closure(tmp_path, monkeypatch):
    directory = tmp_path / SID
    directory.mkdir()
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: tmp_path)
    pid = os.getpid()
    birth = locks.process_start_time(pid)
    assert birth is not None
    hold = directory / f"inuse.{pid}.hold"
    hold.touch()
    assert (pid, birth) not in windows_session_hold.owners(hold)
    assert not session_liveness.session_process_owns_hold(SID, pid, birth)
    with hold.open("r"):
        assert (pid, birth) in windows_session_hold.owners(hold)
        assert session_liveness.session_process_owns_hold(SID, pid, birth)
        assert not session_liveness.session_process_owns_hold(SID, pid, str(int(birth) + 1))
    assert not session_liveness.session_process_owns_hold(SID, pid, birth)


def test_other_file_open_does_not_prove_session_ownership(tmp_path, monkeypatch):
    directory = tmp_path / SID
    directory.mkdir()
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: tmp_path)
    pid = os.getpid()
    birth = locks.process_start_time(pid)
    assert birth is not None
    (directory / f"inuse.{pid}.hold").touch()
    with (directory / "unrelated").open("w"):
        assert not session_liveness.session_process_owns_hold(SID, pid, birth)


def test_windows_metadata_and_standalone_hook_capture(tmp_path, monkeypatch):
    pid = os.getpid()
    process = native_source_context.process(pid)
    assert process["start_time"] == locks.process_start_time(pid)
    assert process["parent_pid"] == os.getppid()
    epoch = native_source_context.epoch()
    assert epoch["platform"] == "windows"
    assert epoch == native_source_context.epoch()
    directory = tmp_path / SID
    directory.mkdir()
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: tmp_path)
    code = (
        "import importlib.util,json,sys;"
        "s=importlib.util.spec_from_file_location('hook_source',sys.argv[1]);"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        "print(json.dumps(m.capture()))"
    )
    with (directory / f"inuse.{pid}.hold").open("w"):
        result = subprocess.run(
            [sys.executable, "-c", code, str(Path(native_source_context.__file__))],
            capture_output=True, text=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, result.stderr
        witness = json.loads(result.stdout)
        assert witness["qualified"], result.stderr
        assert {"pid": pid, "start_time": process["start_time"]} in witness["ancestors"]
        assert native_lifecycle_source.start_identity(SID, witness) == native_source_context.identity(
            SID, pid, process["start_time"], epoch,
        )
    assert native_lifecycle_source.start_identity(SID, witness) is None


@pytest.mark.parametrize("predecessor_visible_at_registration", [True, False])
def test_registered_child_can_be_observed_and_conditionally_promoted(
    tmp_tracking_dir, monkeypatch, monkeypatch_config, predecessor_visible_at_registration,
):
    from dataclasses import asdict

    from agent_worktrees import config as cfg, config_authority, tracking
    from agent_worktrees import conditional_head_replacement as replacement
    from agent_worktrees.tracking_session_registration_write import apply_session_register

    old = "00000000-0000-4000-8000-000000000002"
    root = tmp_tracking_dir / "trees"
    checkout = root / "target"
    checkout.mkdir(parents=True)
    state = tmp_tracking_dir / "native"
    (state / old).mkdir(parents=True)
    (state / old / "events.jsonl").touch()
    config = cfg.Config(
        srcroot=str(tmp_tracking_dir), machine="test", platform=cfg.detect_platform(),
        repo_name="test-repo", repos={"test-repo": cfg.RepoConfig(
            anchor=str(tmp_tracking_dir), worktree_root=str(root),
        )},
    )
    monkeypatch.setattr(config_authority, "load_project_authority_config", lambda *_a, **_k: config)
    monkeypatch.setattr(cfg, "load_project_config", lambda *_a, **_k: config)
    monkeypatch.setattr(tracking, "_owning_tracking_dir", lambda _id: tmp_tracking_dir)
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: state)
    monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: {})
    path = tmp_tracking_dir / "target.yaml"
    record = tracking.WorktreeRecord(
        worktree_id="target", branch="worktree/target", worktree_path=str(checkout),
        repo="test-repo", machine="test", platform=config.platform,
        started_at="2026-01-01T00:00:00", last_resumed_at="2026-01-01T00:00:00",
        resume_count=0, title=None, status="active", completed_at=None,
        creation_nonce="creation-one",
        sessions=[tracking.SessionEntry(old, "2026-01-01T00:00:00")],
    )
    tracking._append_head_transition(record, old, reason="register")
    tracking.save_record(record, path)
    target = replacement.ReplacementTarget(
        "target", str(checkout), "creation-one", old, record.head_revision, record.lifecycle_revision,
    )
    assert replacement.check_dead_head(target) == asdict(target)
    ready, release = tmp_tracking_dir / "ready.json", tmp_tracking_dir / "release"
    code = """
import json, os, pathlib, sys, time
state, sid, checkout, ready, release = sys.argv[1:]
directory = pathlib.Path(state) / sid
directory.mkdir()
(directory / 'events.jsonl').touch()
(directory / 'workspace.yaml').write_text(json.dumps({'cwd': checkout}), encoding='utf-8')
pid = os.getpid()
with (directory / f'inuse.{pid}.hold').open('w'):
    pathlib.Path(ready).write_text(json.dumps({'pid': pid}), encoding='utf-8')
    deadline = time.monotonic() + 45
    while not pathlib.Path(release).exists():
        if time.monotonic() >= deadline:
            raise SystemExit('release deadline exceeded')
        time.sleep(0.02)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(state), SID, str(checkout), str(ready), str(release)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "isolated native holder did not become ready"
        # A venv executable can be a launcher. Use the child's reported process,
        # then corroborate it with the kernel rather than trusting Popen.pid.
        pid = json.loads(ready.read_text(encoding="utf-8"))["pid"]
        birth = locks.process_start_time(pid)
        assert birth
        epoch = native_source_context.epoch()
        witness = {"version": 1, "qualified": True, **epoch,
                   "ancestors": [{"pid": pid, "start_time": birth}]}
        source = native_lifecycle_source.start_identity(SID, witness)
        assert source == native_source_context.identity(SID, pid, birth, epoch)
        if not predecessor_visible_at_registration:
            # A temporary metadata gap makes the normal hook retain its head;
            # the explicit owner can act only after positive death proof returns.
            (state / old).rename(state / "temporarily-unavailable")
        assert apply_session_register({
            "worktree_id": "target", "yaml_path": str(path), "session_id": SID,
            "source": "bind", "source_identity": source, "pid": pid,
        })["ok"]
        if not predecessor_visible_at_registration:
            (state / "temporarily-unavailable").rename(state / old)
        assert tracking.load_record(path).resolved_head_session == (
            SID if predecessor_visible_at_registration else old
        )
        candidate = replacement.observe_candidate("target", SID, pid)
        request = replacement.HeadReplacement(**{
            key: value for key, value in candidate.items() if key != "head_transitions"
        })
        if not predecessor_visible_at_registration:
            result = replacement.replace_dead_head(request)
            assert result.previous_head == old and result.head_session == SID
        readback = replacement.observe_candidate("target", SID, pid)
        assert readback["expected_head"] == SID
        assert [row["session_id"] for row in readback["head_transitions"]] == [old, SID]
        assert readback["head_transitions"][-1]["reason"] == (
            "reclaim" if predecessor_visible_at_registration else "authorized-dead-head-replacement"
        )
        before = path.read_bytes()
        with pytest.raises(ValueError):
            replacement.replace_dead_head(request)
        assert path.read_bytes() == before
        release.touch()
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, (stdout, stderr)
        assert not session_liveness.session_process_owns_hold(SID, pid, birth)
        with pytest.raises((ValueError, OSError)):
            replacement.observe_candidate("target", SID, pid)
    finally:
        release.touch(exist_ok=True)
        if child.poll() is None:
            try:
                child.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate(timeout=5)
