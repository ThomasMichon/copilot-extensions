"""Tests for explicit managed-worktree removal."""

from __future__ import annotations

import argparse
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_worktrees import __main__ as cli
from agent_worktrees import git_ops
from agent_worktrees import tracking


def _record(tmp_path: Path) -> tuple[tracking.WorktreeRecord, Path]:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    record = tracking.WorktreeRecord(
        worktree_id="managed-1",
        branch="worktree/managed-1",
        worktree_path=str(worktree),
        repo="demo",
        machine="machine",
        platform="windows",
        started_at="2026-09-01T00:00:00",
        last_resumed_at="2026-09-01T00:00:00",
        resume_count=0,
        title=None,
        status="finalized",
        completed_at=None,
        sessions=[],
        prs=[],
        kind="bridge",
    )
    tracking_dir = tmp_path / "tracking"
    tracking_dir.mkdir()
    tracking.save_record(record, tracking_dir / "managed-1.yaml")
    return record, tracking_dir


def _config(tmp_path: Path):
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    return types.SimpleNamespace(
        default_repo=types.SimpleNamespace(anchor=str(anchor))
    )


def test_remove_system_retains_record_when_worktree_removal_fails(
    tmp_path,
):
    record, tracking_dir = _record(tmp_path)
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True)

    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.sessions.kill_tmux_session"), \
         patch(
             "agent_worktrees.git_ops.list_worktree_paths",
             return_value=[record.worktree_path],
         ), \
         patch("agent_worktrees.git_ops.remove_worktree", return_value=False), \
         patch("agent_worktrees.__main__._json_output") as json_output:
        result = cli.cmd_remove_system(args)

    assert result == 1
    payload = json_output.call_args.args[0]
    assert "worktree remove failed" in payload["error"]
    assert "tracking record retained for retry" in payload["error"]
    assert (tracking_dir / "managed-1.yaml").exists()
    assert Path(record.worktree_path).exists()


def test_remove_system_deletes_record_after_worktree_removal_succeeds(
    tmp_path,
):
    record, tracking_dir = _record(tmp_path)
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True)

    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.sessions.kill_tmux_session"), \
         patch("agent_worktrees.git_ops.list_worktree_paths"), \
         patch("agent_worktrees.git_ops.remove_worktree", return_value=True), \
         patch("agent_worktrees.git_ops.git") as git, \
         patch("agent_worktrees.disposition_history.remove"), \
         patch("agent_worktrees.activity.log_event"), \
         patch("agent_worktrees.__main__._json_output") as json_output:
        git.return_value.returncode = 0
        result = cli.cmd_remove_system(args)

    assert result == 0
    json_output.assert_called_once_with({"removed": "managed-1"})
    assert not (tracking_dir / "managed-1.yaml").exists()


def test_remove_system_deletes_unregistered_leftover_and_record(
    tmp_path,
):
    record, tracking_dir = _record(tmp_path)
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True)

    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.sessions.kill_tmux_session"), \
         patch("agent_worktrees.git_ops.list_worktree_paths", return_value=[]), \
         patch(
             "agent_worktrees.git_ops.remove_worktree",
             return_value=False,
         ) as remove_worktree, \
         patch("agent_worktrees.git_ops.git") as git, \
         patch("agent_worktrees.disposition_history.remove"), \
         patch("agent_worktrees.activity.log_event"), \
         patch("agent_worktrees.__main__._json_output"):
        git.return_value.returncode = 0
        result = cli.cmd_remove_system(args)

    assert result == 0
    remove_worktree.assert_called_once_with(
        str(tmp_path / "anchor"),
        record.worktree_path,
    )
    assert not Path(record.worktree_path).exists()
    assert not (tracking_dir / "managed-1.yaml").exists()


def test_remove_system_preserves_unregistered_nonempty_path(
    tmp_path,
):
    record, tracking_dir = _record(tmp_path)
    data = Path(record.worktree_path) / "keep.txt"
    data.write_text("unrelated data", encoding="utf-8")
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True)

    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.sessions.kill_tmux_session"), \
         patch("agent_worktrees.git_ops.remove_worktree", return_value=False), \
         patch("agent_worktrees.git_ops.list_worktree_paths", return_value=[]), \
         patch("agent_worktrees.__main__._json_output"):
        result = cli.cmd_remove_system(args)

    assert result == 1
    assert data.read_text(encoding="utf-8") == "unrelated data"
    assert (tracking_dir / "managed-1.yaml").exists()


def test_managed_gc_uses_non_force_removal_for_registered_worktree(
    tmp_path,
):
    record, tracking_dir = _record(tmp_path)
    record.branch = ""
    repo = _config(tmp_path).default_repo

    with patch("agent_worktrees.sessions.has_mux_session", return_value=False), \
         patch(
             "agent_worktrees.sessions.scan_sessions_fast",
             return_value=types.SimpleNamespace(active_sessions={}),
         ), \
         patch(
             "agent_worktrees.git_ops.list_worktree_paths",
             return_value=[record.worktree_path],
         ), \
         patch("agent_worktrees.git_ops.git") as git, \
         patch("agent_worktrees.disposition_history.remove"):
        git.return_value.returncode = 0
        removed, warnings = cli._remove_managed_worktree(
            record,
            repo,
            tracking_dir,
        )

    assert removed is True
    assert warnings == []
    worktree_remove = git.call_args_list[0]
    assert worktree_remove.args[:3] == (
        "worktree",
        "remove",
        record.worktree_path,
    )
    assert "--force" not in worktree_remove.args


def test_remove_system_retains_record_when_registration_probe_fails(
    tmp_path,
):
    record, tracking_dir = _record(tmp_path)
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True)

    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.sessions.kill_tmux_session"), \
         patch("agent_worktrees.git_ops.remove_worktree", return_value=False), \
         patch(
             "agent_worktrees.git_ops.list_worktree_paths",
             side_effect=RuntimeError("registration probe failed"),
         ), \
         patch("agent_worktrees.__main__._json_output"):
        result = cli.cmd_remove_system(args)

    assert result == 1
    assert Path(record.worktree_path).exists()
    assert (tracking_dir / "managed-1.yaml").exists()


def test_remove_system_retains_missing_path_when_still_registered(
    tmp_path,
):
    record, tracking_dir = _record(tmp_path)
    Path(record.worktree_path).rmdir()
    args = argparse.Namespace(worktree_id=record.worktree_id, json=True)

    with patch("agent_worktrees.config.load_config", return_value=_config(tmp_path)), \
         patch("agent_worktrees.config.tracking_dir", return_value=tracking_dir), \
         patch("agent_worktrees.sessions.kill_tmux_session"), \
         patch("agent_worktrees.git_ops.remove_worktree", return_value=False), \
         patch(
             "agent_worktrees.git_ops.list_worktree_paths",
             return_value=[record.worktree_path],
         ), \
         patch("agent_worktrees.__main__._json_output"):
        result = cli.cmd_remove_system(args)

    assert result == 1
    assert (tracking_dir / "managed-1.yaml").exists()


def test_remove_system_retains_record_when_yaml_unlink_fails(
    tmp_path,
):
    record, tracking_dir = _record(tmp_path)
    record.worktree_path = ""
    record.branch = ""

    with patch("pathlib.Path.unlink", side_effect=PermissionError("locked")):
        removed, warnings = cli._remove_managed_worktree(
            record,
            _config(tmp_path).default_repo,
            tracking_dir,
            force=True,
        )

    assert removed is False
    assert warnings == ["tracking record remove failed: locked"]
    assert (tracking_dir / "managed-1.yaml").exists()


def test_worktree_registration_probe_can_fail_closed(tmp_path):
    failure = types.SimpleNamespace(
        returncode=128,
        stdout="",
        stderr="fatal: not a git repository",
    )

    with patch("agent_worktrees.git_ops.git", return_value=failure), \
         pytest.raises(RuntimeError, match="not a git repository"):
        git_ops.list_worktree_paths(cwd=tmp_path, fail_on_error=True)
