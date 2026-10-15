"""Visible foreign-platform ledgers cannot establish claimant death."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import claimant, config as cfg, git_ops, prune, tracking

pytestmark = pytest.mark.guard


@pytest.mark.parametrize("actual,stored", [
    ("linux", "windows"), ("linux", "wsl"),
    ("windows", "linux"), ("wsl", "linux"),
])
@pytest.mark.parametrize("worktree_id", ["parent", "@anchor"])
@pytest.mark.parametrize("status", ["active", "finalized"])
def test_foreign_ledger_is_unconfirmed_and_preserves_claimed_resource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, actual: str, stored: str,
    worktree_id: str, status: str,
) -> None:
    state = tmp_path / "project"
    directory = state / "worktrees"
    directory.mkdir(parents=True)
    config = SimpleNamespace(machine="Example-Host")
    monkeypatch.setattr(cfg, "load_config", lambda: config)
    monkeypatch.setattr(cfg, "project_dir", lambda project=None: state)
    monkeypatch.setattr(cfg, "detect_platform", lambda: actual)
    record = tracking.create_new_record(
        worktree_id, "worktree/parent", str(tmp_path / "removed-checkout"),
        "project", "Example-Host", stored, directory,
    )
    record.status = status
    tracking.save_record(record, record.yaml_path)
    owner = f"Example-Host/project/{worktree_id}"
    child = tracking.create_new_record(
        "child", "worktree/child", str(tmp_path / "child"),
        "project", "Example-Host", actual, directory, owner_ref=owner,
    )
    before = {path: path.read_bytes() for path in directory.glob("*.yaml")}
    assert claimant.local_claimant_alive(owner) is None
    assert claimant.resolve_claimant_alive(owner, allow_remote=False) is None
    info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.UNUSED)
    verdict = prune.assess(
        child, info, turn_count=0, claimant_alive=claimant.local_claimant_alive,
    )
    assert not verdict.safe and verdict.category == "claimed"
    assert "unconfirmed" in verdict.reason
    assert before == {path: path.read_bytes() for path in directory.glob("*.yaml")}
