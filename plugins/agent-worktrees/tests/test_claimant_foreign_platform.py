"""Visible foreign-platform ledgers cannot establish claimant death."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import (
    claimant, config as cfg, git_ops, legacy_wsl_record_locality, prune, tracking,
)

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


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
def test_reference_project_cannot_replace_actual_caller_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    state = tmp_path / "receiving-project"
    directory = state / "worktrees"
    directory.mkdir(parents=True)
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    (anchor / "machines.yaml").write_text("machines: {}\n", encoding="utf-8")
    receiving = cfg.Config(
        srcroot=str(tmp_path), machine="host-b", platform=platform, repo_name="receiving-project",
        repos={"receiving-project": cfg.RepoConfig(
            anchor=str(anchor), worktree_root=str(tmp_path / "trees"),
        )},
    )
    caller = cfg.Config(
        srcroot=str(tmp_path), machine="host-a", platform=platform, repo_name="caller-project",
        repos={"caller-project": cfg.RepoConfig(
            anchor=str(anchor), worktree_root=str(tmp_path / "caller-trees"),
        )},
    )
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: caller)
    monkeypatch.setattr(cfg, "load_project_config", lambda *a, **k: receiving)
    monkeypatch.setattr(cfg, "project_dir", lambda project=None: state)
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    parent = tracking.create_new_record(
        "parent", "worktree/parent", str(tmp_path / "trees" / "parent"),
        receiving.repo_name, "host-b", platform, directory,
    )
    ref = "host-b/receiving-project/parent"
    child = tracking.create_new_record(
        "child", "worktree/child", str(tmp_path / "child"),
        "caller-project", "host-a", platform, directory, owner_ref=ref,
    )
    before = (parent.yaml_path.read_bytes(), child.yaml_path.read_bytes())
    assert not legacy_wsl_record_locality.legacy_wsl_owner_ref_is_local(tracking.parse_claim_ref(ref))
    assert claimant.local_claimant_alive(ref) is None
    assert claimant.resolve_claimant_alive(ref, allow_remote=False) is None
    verdict = prune.assess(
        child, git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.UNUSED),
        turn_count=0, claimant_alive=claimant.local_claimant_alive,
    )
    assert not verdict.safe and verdict.category == "claimed"
    assert "unconfirmed" in verdict.reason
    assert before == (parent.yaml_path.read_bytes(), child.yaml_path.read_bytes())


@pytest.mark.parametrize("qualified", [False, True])
def test_missing_caller_configuration_never_confirms_claimant_death(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qualified: bool,
) -> None:
    def unavailable(*args, **kwargs):
        raise ValueError("caller identity is unavailable")

    monkeypatch.setattr(cfg, "load_config", unavailable)
    monkeypatch.setattr(cfg, "project_dir", lambda project=None: tmp_path)
    ref = "host-a/project/missing" if qualified else "missing"
    assert claimant.local_claimant_alive(ref) is None
    assert claimant.resolve_claimant_alive(ref, allow_remote=False) is None
