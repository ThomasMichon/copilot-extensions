"""Tests for tools/rollback_release.py -- the Phase 4 emergency rollback
tool (pause/resume + revert-the-last-promotion, never a force-push)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import promote_release as pr
import rollback_release as rb

_TOOLS = Path(__file__).resolve().parent
_REQUIRED_TOOLS = ("accumulate_bumps.py", "materialize_main.py", "changefile.py")


def _git(args: list[str], cwd: Path, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(["add", "-A"], repo)
    _git(["-c", "user.email=t@example.com", "-c", "user.name=Test",
          "commit", "-m", message], repo)
    return _git(["rev-parse", "HEAD"], repo)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init", "-q", "-b", "main"], root)
    (root / "tools").mkdir()
    for name in _REQUIRED_TOOLS:
        shutil.copy(_TOOLS / name, root / "tools" / name)
    (root / "plugins" / "demo-plugin").mkdir(parents=True)
    (root / "plugins" / "demo-plugin" / "plugin.json").write_text(
        json.dumps({"name": "demo-plugin", "version": "0.1.0-dev1"}), encoding="utf-8"
    )
    (root / ".github" / "plugin").mkdir(parents=True)
    (root / ".github" / "plugin" / "marketplace.json").write_text(
        json.dumps({
            "name": "x", "metadata": {"description": "t", "version": "1.0.0-dev1"},
            "plugins": [{"name": "demo-plugin", "description": "t",
                         "version": "0.1.0-dev1", "source": "plugins/demo-plugin"}],
        }),
        encoding="utf-8",
    )
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _commit(root, "initial")
    _git(["branch", "dev"], root)
    return root


def _promote_one(repo: Path, *, content: str = "content\n") -> dict:
    _git(["checkout", "-q", "dev"], repo)
    (repo / "plugins" / "demo-plugin" / "change.txt").write_text(content, encoding="utf-8")
    _commit(repo, "demo-plugin: a change")
    _git(["checkout", "-q", "main"], repo)
    report = pr.promote(repo=repo, dev_ref="dev", main_ref="main", push=False)
    assert report["promoted"] is True
    _sync_main(repo, report["commit"])
    return report


def _sync_main(repo: Path, commit: str) -> None:
    """``rollback_release``/``promote_release`` operate through an isolated
    scratch worktree and never touch the caller's real working tree, so a
    test that then wants to build further real commits on top of the
    result must explicitly fast-forward BOTH the ref and the checked-out
    working tree/index -- ``update-ref`` alone leaves the working tree
    stale, which would otherwise silently commit on top of an old parent."""
    _git(["update-ref", "refs/heads/main", commit], repo)
    _git(["checkout", "-q", "main"], repo)
    _git(["reset", "--hard", "main"], repo)


def test_status_reports_fresh_state(repo: Path):
    code = rb.main(["--repo", str(repo), "--main-ref", "main", "status"])
    assert code == 0


def test_pause_lands_a_commit_and_promote_then_refuses(repo: Path):
    result = rb.pause(repo=repo, main_ref="main", reason="investigating", push=False)
    _sync_main(repo, result["commit"])
    state = pr._read_pipeline_state(_git(["rev-parse", "main"], repo), repo=repo)
    assert state["paused"] is True
    assert state["pause_reason"] == "investigating"

    _git(["checkout", "-q", "dev"], repo)
    (repo / "plugins" / "demo-plugin" / "x.txt").write_text("x\n", encoding="utf-8")
    _commit(repo, "demo-plugin: a change")
    _git(["checkout", "-q", "main"], repo)

    with pytest.raises(pr.PromotionPaused, match="investigating"):
        pr.promote(repo=repo, dev_ref="dev", main_ref="main", push=False)


def test_resume_clears_pause(repo: Path):
    paused = rb.pause(repo=repo, main_ref="main", reason="x", push=False)
    _sync_main(repo, paused["commit"])
    resumed = rb.resume(repo=repo, main_ref="main", push=False)
    _sync_main(repo, resumed["commit"])
    state = pr._read_pipeline_state(_git(["rev-parse", "main"], repo), repo=repo)
    assert state["paused"] is False
    assert state["pause_reason"] is None


def test_revert_requires_a_recorded_promotion(repo: Path):
    with pytest.raises(pr.PromotionError, match="no recorded promotion"):
        rb.revert(repo=repo, main_ref="main", reason="test", push=False)


def test_revert_finds_the_promotion_commit_even_through_a_pause_on_top(repo: Path):
    """The real operator flow is pause-THEN-revert, so a pause bookkeeping
    commit is normally sitting on top of the promotion commit by the time
    revert runs -- it must still find and revert the right commit."""
    promotion = _promote_one(repo)
    paused = rb.pause(repo=repo, main_ref="main", reason="x", push=False)
    _sync_main(repo, paused["commit"])

    result = rb.revert(repo=repo, main_ref="main", reason="test", push=False)
    assert result["reverted_commit"] == promotion["commit"]


def test_revert_refuses_when_no_promotion_commit_exists_in_history(repo: Path):
    # A pipeline-state file with a last_promotion entry, but no commit in
    # history actually carries a "promote-" tag -- an inconsistent/tampered
    # state that must not be trusted.
    (repo / ".github").mkdir(exist_ok=True)
    (repo / pr.PIPELINE_STATE_PATH).write_text(
        json.dumps({"paused": False, "last_promotion": {"dev_head": "deadbeef"}}),
        encoding="utf-8",
    )
    _commit(repo, "a hand-crafted state file, not a real promotion")

    with pytest.raises(pr.PromotionError, match="no generated promotion commit"):
        rb.revert(repo=repo, main_ref="main", reason="test", push=False)


def test_revert_reverts_content_tags_and_records_rollback(repo: Path):
    promotion = _promote_one(repo, content="bad content\n")
    before_revert_tree = _git(["rev-parse", "main^{tree}"], repo)

    result = rb.revert(repo=repo, main_ref="main", reason="broke prod", push=False)
    _sync_main(repo, result["revert_commit"])

    # The revert commit's tree must differ from the bad promotion's tree
    # (the offending file must be gone) but the parent chain is preserved
    # (never a force-push/history-rewrite).
    parents = _git(["log", "-1", "--format=%P", result["revert_commit"]], repo).split()
    assert parents == [promotion["commit"]]
    after_tree = _git(["rev-parse", f"{result['revert_commit']}^{{tree}}"], repo)
    assert after_tree != before_revert_tree

    tags = _git(["tag", "--points-at", result["revert_commit"]], repo).splitlines()
    assert result["tag"] in tags
    assert result["tag"].startswith("rollback-")

    state = pr._read_pipeline_state(result["revert_commit"], repo=repo)
    assert state["last_rollback"]["reverted_dev_head"] == promotion["dev_head"]
    assert state["last_rollback"]["reason"] == "broke prod"


def test_full_rollback_cycle_blocks_then_force_allows_repromotion(repo: Path):
    """The end-to-end operator flow: pause -> revert -> dev fixes forward ->
    a same-state re-promote is refused -> --force overrides deliberately."""
    promotion = _promote_one(repo, content="bad content\n")

    paused = rb.pause(repo=repo, main_ref="main", reason="bad release", push=False)
    _sync_main(repo, paused["commit"])
    result = rb.revert(repo=repo, main_ref="main", reason="bad release", push=False)
    _sync_main(repo, result["revert_commit"])
    resumed = rb.resume(repo=repo, main_ref="main", push=False)
    _sync_main(repo, resumed["commit"])

    with pytest.raises(pr.NonIncrementalPromotion):
        pr.promote(repo=repo, dev_ref="dev", main_ref="main", push=False, force=False)

    report = pr.promote(repo=repo, dev_ref="dev", main_ref="main", push=False, force=True)
    assert report["promoted"] is True
    assert report["dev_head"] == promotion["dev_head"]
