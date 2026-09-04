from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_worktrees import __main__ as cli
from agent_worktrees import finalize, git_ops, tracking


def test_active_ahp_binding_blocks_finalize(monkeypatch):
    monkeypatch.setattr(
        finalize.sessions,
        "worktree_has_live_session",
        lambda _record: False,
    )
    monkeypatch.setattr(
        finalize.sessions,
        "has_mux_session",
        lambda _worktree_id: False,
    )
    record = SimpleNamespace(
        worktree_id="host-win-20260903-abcd",
        session_backend=SimpleNamespace(state="active"),
    )
    assert finalize._has_live_session(record) is True


def test_disposed_ahp_binding_does_not_block_finalize(monkeypatch):
    monkeypatch.setattr(
        finalize.sessions,
        "worktree_has_live_session",
        lambda _record: False,
    )
    monkeypatch.setattr(
        finalize.sessions,
        "has_mux_session",
        lambda _worktree_id: False,
    )
    record = SimpleNamespace(
        worktree_id="host-win-20260903-abcd",
        session_backend=SimpleNamespace(state="disposed"),
    )
    assert finalize._has_live_session(record) is False


def test_opaque_future_backend_blocks_finalize(monkeypatch):
    monkeypatch.setattr(
        finalize.sessions,
        "worktree_has_live_session",
        lambda _record: False,
    )
    record = SimpleNamespace(
        worktree_id="host-win-20260903-abcd",
        session_backend=None,
        session_backend_opaque=True,
    )
    assert finalize._has_live_session(record) is True


def _record(tmp_path, *, state: str) -> tracking.WorktreeRecord:
    record = tracking.WorktreeRecord(
        worktree_id="host-win-20260903-abcd",
        branch="worktree/host-win-20260903-abcd",
        worktree_path=str(tmp_path / "worktree"),
        repo="example",
        machine="host",
        platform="windows",
        started_at="2026-09-03T00:00:00+00:00",
        last_resumed_at="2026-09-03T00:00:00+00:00",
        resume_count=0,
        title=None,
        status="finalized",
        completed_at="2026-09-03T00:00:01+00:00",
    )
    record.session_backend = tracking.SessionBackendBinding(
        kind="ahp",
        endpoint_url="ws://127.0.0.1:8765",
        session_id="11111111-1111-1111-1111-111111111111",
        protocol_version="0.7.0",
        auth_account="octocat",
        created_at="2026-09-03T00:00:00+00:00",
        last_seen_at="2026-09-03T00:00:00+00:00",
        state=state,
    )
    return record


def _configure_reap(monkeypatch, tmp_path, record):
    Path(record.worktree_path).mkdir(parents=True)
    record_path = tmp_path / f"{record.worktree_id}.yaml"
    tracking.save_record(record, record_path)
    repo = SimpleNamespace(
        remote="origin",
        default_branch="main",
        anchor=str(tmp_path),
        worktree_root=str(tmp_path),
    )
    monkeypatch.setattr(
        cli.cfg,
        "load_config",
        lambda: SimpleNamespace(default_repo=repo),
    )
    monkeypatch.setattr(cli.cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "_resolve_worktree_id", lambda value: value)
    monkeypatch.setattr(cli.git_ops, "has_remote", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        cli.sessions,
        "scan_sessions_fast",
        lambda _records: SimpleNamespace(active_sessions={}, turn_count={}),
    )
    monkeypatch.setattr(cli.sessions, "_list_mux_sessions", lambda: {})
    monkeypatch.setattr(cli.reclaim, "live_bridge_worktrees", lambda: set())
    return record_path


def test_force_cleanup_refuses_active_hosted_binding(monkeypatch, tmp_path):
    record = _record(tmp_path, state="active")
    record_path = _configure_reap(monkeypatch, tmp_path, record)
    monkeypatch.setattr(
        cli.git_ops,
        "classify_worktree",
        lambda *_args, active_paths, **_kwargs: git_ops.WorktreeStateInfo(
            state=(
                git_ops.WorktreeState.ACTIVE
                if str(tmp_path / "worktree") in active_paths
                else git_ops.WorktreeState.COMPLETED
            )
        ),
    )
    monkeypatch.setattr(
        cli,
        "_reap_worktree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not reap active hosted session")
        ),
    )

    result = cli.reap_one(record.worktree_id, force=True)

    assert record_path.exists()
    assert result["bucket"] == "active"
    assert result["removed"] is False


def test_cleanup_rechecks_hosted_binding_under_finalize_lock(
    monkeypatch,
    tmp_path,
):
    record = _record(tmp_path, state="disposed")
    record_path = _configure_reap(monkeypatch, tmp_path, record)
    monkeypatch.setattr(
        cli.git_ops,
        "classify_worktree",
        lambda *_args, **_kwargs: git_ops.WorktreeStateInfo(
            state=git_ops.WorktreeState.COMPLETED
        ),
    )

    class ActivatingLock:
        def __init__(self, _path):
            pass

        def acquire(self):
            latest = tracking.load_record(record_path)
            latest.session_backend.state = "active"
            latest.session_backend.binding_revision += 1
            tracking.save_record(latest, record_path)

        def release(self):
            pass

    monkeypatch.setattr(cli.fin, "FinalizeLock", ActivatingLock)
    monkeypatch.setattr(
        cli,
        "_reap_worktree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must recheck before reaping")
        ),
    )

    result = cli.reap_one(record.worktree_id, force=True)

    assert result["bucket"] == "active"
    assert result["removed"] is False
