"""Failed physical reap never revokes trust from a retained checkout."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_worktrees import cleanup_reap, permissions


@pytest.mark.parametrize("removed", [False, True])
def test_cleanup_trust_follows_actual_directory_removal(
    tmp_path, monkeypatch, sample_record, removed,
):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    home = tmp_path / ".copilot"
    monkeypatch.setattr(permissions, "_copilot_dir", lambda: home)
    permissions.ensure_trusted_folder(str(checkout))
    record = replace(sample_record, worktree_path=str(checkout))
    monkeypatch.setattr(cleanup_reap, "retirement_blocker", lambda record: None)
    monkeypatch.setattr(cleanup_reap.sessions, "kill_tmux_session", lambda *a: None)
    monkeypatch.setattr(cleanup_reap.procs, "terminate_processes_under", lambda *a: [])

    def remove(*args, **kwargs):
        if removed:
            checkout.rmdir()
        return removed

    monkeypatch.setattr(cleanup_reap.git_ops, "remove_worktree", remove)
    monkeypatch.setattr(cleanup_reap.git_ops, "delete_branch", lambda *a, **k: True)
    monkeypatch.setattr(cleanup_reap.shutil, "rmtree", lambda *a, **k: None)
    monkeypatch.setattr(cleanup_reap.time, "sleep", lambda *a: None)
    monkeypatch.setattr(cleanup_reap.tracking, "retire_record", lambda *a: False)
    monkeypatch.setattr(cleanup_reap.activity, "log_event", lambda *a, **k: None)
    failures, warnings = cleanup_reap._reap_locked(
        record, SimpleNamespace(state=SimpleNamespace(value="completed")),
        SimpleNamespace(anchor=str(tmp_path / "anchor")), tmp_path,
    )
    assert failures == (0 if removed else 1)
    assert any("Directory still present" in warning for warning in warnings) is not removed
    _, data = permissions._read_config_jsonc(home / "config.json")
    assert (str(checkout.resolve()) in data["trustedFolders"]) is not removed
