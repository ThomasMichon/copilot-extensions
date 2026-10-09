"""Typed retry staging uses the existing daemon and the Copilot handoff fence."""

from __future__ import annotations

from types import SimpleNamespace
import argparse
import dataclasses
import json
from pathlib import Path

import pytest

from agent_worktrees import launch_seed_exec, launch_seed_state as state, tracking, tracking_write


def _record(tmp_path):
    return tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "checkout"), "demo", "test",
        "windows", tmp_path,
    ).yaml_path


@pytest.mark.parametrize("kind", ["new", "resume"])
def test_real_daemon_retains_failed_attempt_and_finishes_exactly_one_handoff(
    tmp_path, monkeypatch, kind,
):
    path = _record(tmp_path)
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    endpoint = tracking_write.rendezvous_fields(server)
    def dispatch(verb, args):
        return tracking_write.dispatch(
            verb, args, read_lock_data=lambda: endpoint, ensure_monitor=None, boot_wait_s=0,
        )
    monkeypatch.setattr(state, "_dispatch", dispatch)
    monkeypatch.setattr(
        tracking_write, "run_direct",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must use the real daemon")),
    )
    try:
        seed = state.stage(path, kind=kind, text="quotes ' \"\nsecond line")
        assert state.state_path(path) == tmp_path / "wt-a" / "launch-seed.json"
        assert state.peek(path) == seed
        first = state.take(path, seed_id=seed.seed_id)
        assert state.peek(path).text == seed.text
        assert state.peek(path).handoff_id
        assert state.take(path, seed_id=seed.seed_id)["seed"] is None
        assert state.restore(path, first)["restored"]
        assert state.peek(path).handoff_id is None
        second = state.take(path, seed_id=seed.seed_id)
        assert state.finish(path, second)["finished"]
        assert state.peek(path) is None
        assert state.finish(path, second)["finished"]
    finally:
        server.close()


@pytest.mark.parametrize("kind", ["new", "resume"])
def test_seed_is_discarded_after_backend_start_not_before(tmp_path, monkeypatch, kind):
    path = _record(tmp_path)
    seed = state.stage(path, kind=kind, text="next turn")
    argv = []
    def spawn(command, **kwargs):
        pending = state.peek(path)
        assert pending.text == seed.text and pending.handoff_id
        argv.append(command)
        return SimpleNamespace(wait=lambda: 0)
    monkeypatch.setattr(launch_seed_exec.subprocess, "Popen", spawn)
    assert launch_seed_exec.launch(path, ["copilot"], invoke=True, seed_id=seed.seed_id) == 0
    assert argv == [["copilot", "--interactive", seed.text]]
    assert state.peek(path) is None


@pytest.mark.parametrize("kind", ["new", "resume"])
def test_backend_start_failure_releases_retry_without_losing_text(tmp_path, monkeypatch, kind):
    path = _record(tmp_path)
    seed = state.stage(path, kind=kind, text="next turn")
    def fail(*args, **kwargs):
        raise FileNotFoundError("backend disappeared")
    monkeypatch.setattr(launch_seed_exec.subprocess, "Popen", fail)
    assert launch_seed_exec.launch(path, ["copilot"], invoke=True, seed_id=seed.seed_id) == 3
    assert state.peek(path).text == seed.text
    assert state.peek(path).seed_id == seed.seed_id
    assert state.peek(path).handoff_id is None


def test_stale_handoff_never_erases_or_restores_over_newer_intent(tmp_path):
    path = _record(tmp_path)
    old = state.stage(path, kind="new", text="first task")
    receipt = state.take(path, seed_id=old.seed_id)
    newer = state.stage(path, kind="resume", text="newer request")
    assert state.finish(path, receipt)["reason"] == "superseded"
    assert state.restore(path, receipt)["reason"] == "superseded"
    assert state.peek(path) == newer
    newer_receipt = state.take(path, seed_id=newer.seed_id)
    state.finish(path, newer_receipt)
    assert not state.restore(path, receipt)["restored"]
    assert state.peek(path) is None


def test_new_fallback_cannot_take_a_resume_seed(tmp_path):
    path = _record(tmp_path)
    resume = state.stage(path, kind="resume", text="resume request")
    assert state.claim_creation(path)["seed"] is None
    assert state.peek(path) == resume


def test_legacy_new_seed_migrates_without_two_owners(tmp_path):
    path = _record(tmp_path)
    record = tracking.load_record(path)
    record.pending_seed = "legacy creation prompt"
    record.pending_seed_revision += 1
    tracking.save_record(record, path)
    seed = state.pending(path)
    assert seed.kind == "new" and seed.text == "legacy creation prompt"
    assert tracking.load_record(path).pending_seed is None
    assert state.pending(path) == seed


def test_uncertain_cleanup_retains_handoff_without_resubmission(tmp_path, monkeypatch, capsys):
    path = _record(tmp_path)
    seed = state.stage(path, kind="resume", text="next turn")
    calls = []
    monkeypatch.setattr(
        launch_seed_exec.subprocess, "Popen",
        lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(wait=lambda: 0),
    )
    monkeypatch.setattr(
        state, "finish",
        lambda *a, **k: (_ for _ in ()).throw(tracking_write.AmbiguousWriteOutcome("late ack")),
    )
    assert launch_seed_exec.launch(path, ["copilot"], invoke=True, seed_id=seed.seed_id) == 3
    assert state.peek(path).text == seed.text and state.peek(path).handoff_id
    assert "cleanup is unconfirmed" in capsys.readouterr().err
    assert launch_seed_exec.launch(path, ["copilot"], invoke=True, seed_id=seed.seed_id) == 3
    assert len(calls) == 1


def test_saved_completion_tombstone_cannot_resurrect_prompt(tmp_path, monkeypatch):
    from agent_worktrees.resume_seed import seed_for_attempt

    path = _record(tmp_path)
    seed = state.stage(path, kind="new", text="must not repeat")
    receipt = state.take(path, seed_id=seed.seed_id)
    target = state.state_path(path)
    real_unlink = Path.unlink
    def fail_seed_unlink(self, *args, **kwargs):
        if self == target:
            raise OSError("interrupted after tombstone save")
        return real_unlink(self, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_seed_unlink)
    with pytest.raises(OSError, match="tombstone"):
        state.finish(path, receipt)
    assert "must not repeat" not in target.read_text(encoding="utf-8")
    assert state.peek(path) is None
    assert state.pending(path) is None
    record = tracking.load_record(path)
    assert state.creation_text(path, record) is None
    assert seed_for_attempt(path, record, argparse.Namespace()) is None
    assert state.stage(path) is None
    assert state.take(path, seed_id=seed.seed_id)["seed"] is None
    assert not state.restore(path, receipt)["restored"]


def test_legacy_revision_tombstone_suppresses_extant_old_json(tmp_path):
    path = _record(tmp_path)
    seed = state.stage(path, kind="resume", text="already handed off")
    record = tracking.load_record(path)
    record.pending_seed_revision = seed.revision + 1
    tracking.save_record(record, path)
    state.state_path(path).write_text(
        json.dumps({"version": 1, **dataclasses.asdict(seed)}), encoding="utf-8",
    )
    assert state.peek(path) is None
    assert state.pending(path) is None
    assert state.stage(path) is None


def test_successful_managed_reap_removes_only_owned_seed_state(tmp_path, monkeypatch):
    from agent_worktrees import reap_cli

    path = _record(tmp_path)
    state.stage(path, kind="resume", text="private pending prompt")
    other = state.state_path(path).parent / "unrelated-state.txt"
    other.write_text("keep me", encoding="utf-8")
    record = tracking.load_record(path)
    record.branch = ""
    record.worktree_path = ""
    monkeypatch.setattr(reap_cli.sessions, "has_mux_session", lambda *a: False)
    monkeypatch.setattr(
        reap_cli.sessions, "scan_sessions_fast",
        lambda *a: SimpleNamespace(active_sessions=set()),
    )
    monkeypatch.setattr(reap_cli.disposition_history, "remove", lambda *a: None)
    monkeypatch.setattr(reap_cli.handoff_trace, "remove_trace", lambda *a: None)
    removed, warnings = reap_cli._remove_managed_worktree(
        record, SimpleNamespace(anchor=str(tmp_path)), tmp_path,
    )
    assert removed and not warnings
    assert not path.exists() and not state.state_path(path).exists()
    assert other.read_text(encoding="utf-8") == "keep me"
