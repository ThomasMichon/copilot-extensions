from __future__ import annotations

import argparse
from types import SimpleNamespace

from agent_worktrees import __main__ as cli
from agent_worktrees import tracking
from agent_worktrees.config import SessionBackendConfig

WORKTREE_ID = "host-win-20260903-abcd"


def _record(tmp_path) -> tracking.WorktreeRecord:
    return tracking.WorktreeRecord(
        worktree_id=WORKTREE_ID,
        branch=f"worktree/{WORKTREE_ID}",
        worktree_path=str(tmp_path / "worktree"),
        repo="example",
        machine="host",
        platform="windows",
        started_at="2026-09-03T00:00:00+00:00",
        last_resumed_at="2026-09-03T00:00:00+00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
    )


def _binding(*, state: str = "active") -> tracking.SessionBackendBinding:
    return tracking.SessionBackendBinding(
        kind="ahp",
        endpoint_url="ws://127.0.0.1:8765",
        session_id="11111111-1111-1111-1111-111111111111",
        protocol_version="0.7.0",
        auth_account="octocat",
        created_at="2026-09-03T00:00:01+00:00",
        last_seen_at="2026-09-03T00:00:02+00:00",
        state=state,
    )


def _configure(
    monkeypatch,
    tmp_path,
    outputs,
    events,
    *,
    backend=None,
) -> None:
    backend = backend or SessionBackendConfig(
        kind="ahp",
        endpoint_url="ws://127.0.0.1:8765",
        github_account="octocat",
    )
    repo = SimpleNamespace(worktree_root=str(tmp_path))
    monkeypatch.setattr(
        cli.cfg,
        "load_config",
        lambda: SimpleNamespace(
            session_backend=backend,
            repo_name="example",
            default_repo=repo,
            repos={"example": repo},
        ),
    )
    monkeypatch.setattr(cli.cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "_resolve_worktree_id", lambda value: value)
    monkeypatch.setattr(cli, "_json_output", outputs.append)
    monkeypatch.setattr(
        cli.activity,
        "log_event",
        lambda name, **fields: events.append((name, fields)),
    )


def test_session_backend_ensure_keeps_network_outside_record_lock(
    monkeypatch,
    tmp_path,
):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    tracking.save_record(_record(tmp_path), yaml_path)
    outputs = []
    events = []
    _configure(monkeypatch, tmp_path, outputs, events)

    def ensure(_config, _record):
        with tracking._RecordLock(yaml_path, blocking=False) as lock:
            assert lock.acquired
            concurrent = tracking.load_record(yaml_path)
            concurrent.title = "concurrent update"
            tracking._save_record_unlocked(concurrent, yaml_path)
        return _binding()

    monkeypatch.setattr(
        "agent_worktrees.ahp_backend.ensure_worktree_session",
        ensure,
    )

    rc = cli.cmd_session_backend(argparse.Namespace(
        action="ensure",
        worktree_id=WORKTREE_ID,
    ))

    assert rc == 0
    persisted = tracking.load_record(yaml_path)
    assert persisted.title == "concurrent update"
    assert persisted.session_backend == _binding()
    assert outputs[-1]["session_id"] == _binding().session_id
    assert events == [(
        "ahp_session_bound",
        {
            "worktree_id": WORKTREE_ID,
            "session_id": _binding().session_id,
            "backend": "ahp",
        },
    )]


def test_session_backend_dispose_persists_terminal_binding(
    monkeypatch,
    tmp_path,
):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    record = _record(tmp_path)
    record.session_backend = _binding()
    tracking.save_record(record, yaml_path)
    outputs = []
    events = []
    _configure(monkeypatch, tmp_path, outputs, events)

    def dispose(_config, current):
        current.session_backend.state = "disposed"
        current.session_backend.binding_revision += 1
        return True

    monkeypatch.setattr(
        "agent_worktrees.ahp_backend.dispose_worktree_session",
        dispose,
    )

    rc = cli.cmd_session_backend(argparse.Namespace(
        action="dispose",
        worktree_id=WORKTREE_ID,
    ))

    assert rc == 0
    persisted = tracking.load_record(yaml_path)
    assert persisted.session_backend.state == "disposed"
    assert outputs[-1]["bound"] is False
    assert events[0][0] == "ahp_session_disposed"


def test_direct_backend_refuses_active_hosted_binding(monkeypatch, tmp_path):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    record = _record(tmp_path)
    record.session_backend = _binding()
    tracking.save_record(record, yaml_path)
    outputs = []
    _configure(
        monkeypatch,
        tmp_path,
        outputs,
        [],
        backend=SessionBackendConfig(),
    )

    rc = cli.cmd_session_backend(argparse.Namespace(
        action="ensure",
        worktree_id=WORKTREE_ID,
    ))

    assert rc == 3
    assert "restore the AHP configuration" in outputs[-1]["error"]


def test_status_exposes_configured_account_before_binding(
    monkeypatch,
    tmp_path,
):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    tracking.save_record(_record(tmp_path), yaml_path)
    outputs = []
    _configure(monkeypatch, tmp_path, outputs, [])

    rc = cli.cmd_session_backend(argparse.Namespace(
        action="status",
        worktree_id=WORKTREE_ID,
    ))

    assert rc == 0
    assert outputs[-1] == {
        "enabled": True,
        "kind": "ahp",
        "bound": False,
        "endpoint_url": "ws://127.0.0.1:8765",
        "auth_account": "octocat",
    }


def test_alternate_launch_paths_fail_closed_for_ahp(tmp_path):
    record = _record(tmp_path)
    config = SimpleNamespace(session_backend=SessionBackendConfig(
        kind="ahp",
        endpoint_url="ws://127.0.0.1:8765",
        github_account="octocat",
    ))

    assert "does not yet support" in cli._unsupported_hosted_launch(
        config,
        record,
        "embody",
    )


def test_ensure_rejects_finalizing_worktree(monkeypatch, tmp_path):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    record = _record(tmp_path)
    record.status = "finalizing"
    tracking.save_record(record, yaml_path)
    outputs = []
    _configure(monkeypatch, tmp_path, outputs, [])
    monkeypatch.setattr(
        "agent_worktrees.ahp_backend.ensure_worktree_session",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("must reject before network activation")
        ),
    )

    rc = cli.cmd_session_backend(argparse.Namespace(
        action="ensure",
        worktree_id=WORKTREE_ID,
    ))

    assert rc == 3
    assert "finalizing or finalized" in outputs[-1]["error"]
