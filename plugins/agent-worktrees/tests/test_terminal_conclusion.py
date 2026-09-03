from __future__ import annotations

import json
import os
import types
from pathlib import Path

from agent_worktrees import git_ops, sessions, tracking
from agent_worktrees import terminal_conclusion as tc
from agent_worktrees import __main__ as cli


def _git(cwd: Path, *args: str) -> str:
    return git_ops.git(*args, cwd=cwd).stdout.strip()


def _worker(tmp_path: Path, monkeypatch):
    remote = tmp_path / "remote.git"
    anchor = tmp_path / "anchor"
    root = tmp_path / "worktrees"
    tracking_dir = tmp_path / "tracking"
    worktree_id = "worker-20260901-abcd"
    worktree = root / worktree_id
    tracking_dir.mkdir()
    root.mkdir()

    git_ops.git("init", "--bare", "-b", "main", str(remote))
    git_ops.git("init", "-b", "main", str(anchor))
    _git(anchor, "config", "user.email", "test@example.com")
    _git(anchor, "config", "user.name", "Test")
    (anchor / "README.md").write_text("base\n", encoding="utf-8")
    _git(anchor, "add", "-A")
    _git(anchor, "commit", "-m", "initial")
    _git(anchor, "remote", "add", "origin", str(remote))
    _git(anchor, "push", "-u", "origin", "main")
    git_ops.git(
        "worktree",
        "add",
        str(worktree),
        "-b",
        f"worktree/{worktree_id}",
        "origin/main",
        cwd=anchor,
    )
    _git(worktree, "config", "user.email", "test@example.com")
    _git(worktree, "config", "user.name", "Test")

    record = tracking.create_new_record(
        worktree_id,
        f"worktree/{worktree_id}",
        str(worktree),
        "demo",
        "host",
        "linux",
        tracking_dir,
    )
    record.sessions = [
        tracking.SessionEntry(
            session_id="session-exact",
            started_at="2026-09-01T00:00:00Z",
        )
    ]
    record.head_session = "session-exact"
    tracking.save_record(record, tracking_dir / f"{worktree_id}.yaml")

    monkeypatch.setattr(sessions, "has_mux_session", lambda _worktree: False)
    monkeypatch.setattr(
        sessions,
        "scan_sessions_fast",
        lambda _records: types.SimpleNamespace(active_sessions={}),
    )
    repo = types.SimpleNamespace(
        anchor=str(anchor),
        worktree_root=str(root),
        remote="origin",
        default_branch="main",
    )
    return repo, tracking_dir / f"{worktree_id}.yaml", worktree


def _conclude(record_path: Path, repo, **over):
    return tc.conclude_disposable_worktree(
        record_path,
        repo,
        session_id=over.get("session_id", "session-exact"),
        owner=over.get("owner", "dispatcher"),
        policy=tc.DISPOSABLE_CLI_POLICY,
    )


def test_live_session_is_preserved(tmp_path, monkeypatch):
    repo, record_path, worktree = _worker(tmp_path, monkeypatch)
    monkeypatch.setattr(sessions, "has_mux_session", lambda _worktree: True)
    (worktree / "valuable.txt").write_text("keep\n", encoding="utf-8")

    result = _conclude(record_path, repo)

    record = tracking.load_record(record_path)
    assert result["action"] == "skipped"
    assert result["reason"] == "live-mux"
    assert record.kind == "session"
    assert record.session_entry("session-exact").state == "active"
    assert (worktree / "valuable.txt").exists()


def test_different_lifecycle_head_is_preserved(tmp_path, monkeypatch):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)

    result = _conclude(
        record_path,
        repo,
        session_id="different-session",
    )

    record = tracking.load_record(record_path)
    assert result["action"] == "skipped"
    assert result["reason"] == "session-mismatch"
    assert record.kind == "session"
    assert record.resolved_head_session == "session-exact"
    assert record.session_entry("session-exact").state == "active"


def test_pending_handoff_preserves_predecessor(tmp_path, monkeypatch):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    record = tracking.load_record(record_path)
    tracking.open_handoff(
        record,
        "session-exact",
        "handoff-token",
        save=False,
    )
    tracking.save_record(record, record_path)

    result = _conclude(record_path, repo)

    record = tracking.load_record(record_path)
    assert result["action"] == "skipped"
    assert result["reason"] == "pending-handoff"
    assert record.kind == "session"
    assert record.session_entry("session-exact").state == "active"
    assert record.pending_handoffs


def test_follow_up_preserves_session_and_worktree(tmp_path, monkeypatch):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    record = tracking.load_record(record_path)
    record.follow_up = True
    tracking.save_record(record, record_path)

    result = _conclude(record_path, repo)

    record = tracking.load_record(record_path)
    assert result["reason"] == "follow-up"
    assert record.kind == "session"
    assert record.session_entry("session-exact").state == "active"


def test_open_pr_preserves_session_and_worktree(tmp_path, monkeypatch):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    record = tracking.load_record(record_path)
    record.pr = tracking.PRRecord(state="open", branch="feature/example", number=7)
    tracking.save_record(record, record_path)

    result = _conclude(record_path, repo)

    record = tracking.load_record(record_path)
    assert result["reason"] == "open-pull-request"
    assert record.kind == "session"
    assert record.session_entry("session-exact").state == "active"


def test_non_cli_worktree_preserves_session(tmp_path, monkeypatch):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    record = tracking.load_record(record_path)
    record.interface = "acp"
    tracking.save_record(record, record_path)

    result = _conclude(record_path, repo)

    record = tracking.load_record(record_path)
    assert result["reason"] == "not-cli-worktree"
    assert record.session_entry("session-exact").state == "active"


def test_lifecycle_change_during_git_inspection_blocks_priming(
    tmp_path,
    monkeypatch,
):
    repo, record_path, worktree = _worker(tmp_path, monkeypatch)
    original_current_branch = git_ops.current_branch
    changed = False

    def change_lifecycle(path):
        nonlocal changed
        if not changed:
            changed = True
            record = tracking.load_record(record_path)
            record.sessions.append(
                tracking.SessionEntry(
                    session_id="successor-session",
                    started_at="2026-09-01T00:01:00Z",
                )
            )
            tracking.set_head_session(
                record,
                "successor-session",
                save=False,
            )
            tracking.save_record(record, record_path)
        return original_current_branch(path)

    monkeypatch.setattr(git_ops, "current_branch", change_lifecycle)

    result = _conclude(record_path, repo)

    record = tracking.load_record(record_path)
    assert result["action"] == "skipped"
    assert result["reason"] == "lifecycle-changed"
    assert record.kind == "session"
    assert record.resolved_head_session == "successor-session"
    assert worktree.exists()


def test_dirty_author_work_is_preserved(tmp_path, monkeypatch):
    repo, record_path, worktree = _worker(tmp_path, monkeypatch)
    (worktree / "valuable.txt").write_text("keep\n", encoding="utf-8")

    result = _conclude(record_path, repo)

    record = tracking.load_record(record_path)
    assert result["action"] == "skipped"
    assert result["reason"] == "dirty-work"
    assert record.kind == "session"
    assert record.session_entry("session-exact").state == "active"
    assert (worktree / "valuable.txt").read_text(encoding="utf-8") == "keep\n"


def test_committed_author_work_is_preserved(tmp_path, monkeypatch):
    repo, record_path, worktree = _worker(tmp_path, monkeypatch)
    (worktree / "valuable.txt").write_text("keep\n", encoding="utf-8")
    _git(worktree, "add", "valuable.txt")
    _git(worktree, "commit", "-m", "valuable work")
    before = _git(worktree, "rev-parse", "HEAD")

    result = _conclude(record_path, repo)

    assert result["action"] == "skipped"
    assert result["reason"] == "local-commits"
    assert _git(worktree, "rev-parse", "HEAD") == before
    assert tracking.load_record(record_path).kind == "session"


def test_missing_checkout_with_committed_branch_is_preserved(
    tmp_path,
    monkeypatch,
):
    repo, record_path, worktree = _worker(tmp_path, monkeypatch)
    (worktree / "valuable.txt").write_text("keep\n", encoding="utf-8")
    _git(worktree, "add", "valuable.txt")
    _git(worktree, "commit", "-m", "valuable work")
    git_ops.git(
        "worktree",
        "remove",
        str(worktree),
        "--force",
        cwd=repo.anchor,
    )

    result = _conclude(record_path, repo)

    assert result["action"] == "skipped"
    assert result["reason"] == "local-commits"
    assert tracking.load_record(record_path).kind == "session"
    assert (
        git_ops.git(
            "show-ref",
            "--verify",
            "refs/heads/worktree/worker-20260901-abcd",
            cwd=repo.anchor,
            check=False,
        ).returncode
        == 0
    )


def test_generated_overlay_is_preserved_as_dirty_work(tmp_path, monkeypatch):
    repo, record_path, worktree = _worker(tmp_path, monkeypatch)
    overlay = worktree / ".github" / "copilot" / "settings.local.json"
    overlay.parent.mkdir(parents=True)
    managed = {
        "example": {
            "source": {"source": "directory", "path": "/path/to/marketplace"}
        }
    }
    overlay.write_text(
        json.dumps(
            {
                "extraKnownMarketplaces": managed,
                "_agentWorktreesMarketplaceOverrides": {
                    "version": 1,
                    "marketplaces": managed,
                },
            }
        ),
        encoding="utf-8",
    )

    result = _conclude(record_path, repo)

    record = tracking.load_record(record_path)
    assert result["action"] == "skipped"
    assert result["reason"] == "dirty-work"
    assert overlay.exists()
    assert record.kind == "session"
    assert record.status == "active"
    assert record.session_entry("session-exact").state == "active"


def test_repeated_conclusion_is_idempotent(tmp_path, monkeypatch):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)

    first = _conclude(record_path, repo)
    second = _conclude(record_path, repo)

    assert first["action"] == "primed"
    assert second["action"] == "already-primed"
    assert second["session"]["action"] == "already-concluded"


def test_managed_gc_can_later_remove_primed_tree(tmp_path, monkeypatch):
    repo, record_path, worktree = _worker(tmp_path, monkeypatch)
    _conclude(record_path, repo)
    config = types.SimpleNamespace(default_repo=repo, repo_name="demo")
    monkeypatch.setattr(cli.cfg, "load_config", lambda: config)
    monkeypatch.setattr(cli.cfg, "tracking_dir", lambda: record_path.parent)
    monkeypatch.setattr(
        cli.sessions, "_list_mux_sessions", lambda: {}
    )
    monkeypatch.setattr(
        cli.sessions, "_mux_session_activity", lambda: {}
    )
    monkeypatch.setattr(
        cli.sessions,
        "scan_sessions_fast",
        lambda _records: types.SimpleNamespace(active_sessions={}),
    )
    monkeypatch.setattr(cli, "_build_active_paths", lambda *_args, **_kw: set())

    report = cli.sweep_managed_worktrees(min_idle_secs=0)

    assert [entry["id"] for entry in report["removed"]] == [
        "worker-20260901-abcd"
    ]
    assert not worktree.exists()
    assert not record_path.exists()


def test_short_wait_does_not_break_a_fresh_lifecycle_lock(
    tmp_path,
    monkeypatch,
    capsys,
):
    lock_path = tmp_path / ".finalize.lock"
    lock_path.write_text(str(os.getpid()), encoding="utf-8")
    os.utime(lock_path, (100.0, 100.0))
    monkeypatch.setattr(
        "agent_worktrees.finalize.time.time",
        lambda: 110.0,
    )
    lock = tc.finalize.FinalizeLock(
        lock_path,
        timeout=0.01,
        stale_after=60.0,
    )

    try:
        lock.acquire()
    except TimeoutError:
        pass
    else:
        raise AssertionError("fresh lifecycle lock should not be acquired")

    assert lock_path.exists()
    captured = capsys.readouterr()
    assert captured.out == ""


def test_reused_pid_does_not_keep_a_stale_lifecycle_lock(
    tmp_path,
    monkeypatch,
):
    lock_path = tmp_path / ".finalize.lock"
    lock_path.write_text("123:111:token", encoding="utf-8")
    monkeypatch.setattr("agent_worktrees.finalize.locks.pid_alive", lambda _pid: True)
    monkeypatch.setattr(
        "agent_worktrees.finalize.locks.process_start_time",
        lambda _pid: "222",
    )
    lock = tc.finalize.FinalizeLock(
        lock_path,
        timeout=0.1,
        stale_after=3600,
    )

    lock.acquire()
    try:
        assert lock_path.read_text(encoding="utf-8") == lock.token
    finally:
        lock.release()
