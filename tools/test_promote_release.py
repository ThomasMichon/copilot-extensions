"""Tests for tools/promote_release.py -- the Phase 3 dev->main promotion
tool (wholesale tree-replace commit, never a merge)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import promote_release as pr

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


def _write_plugin(repo: Path, plugin: str, version: str) -> None:
    d = repo / "plugins" / plugin
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.json").write_text(
        json.dumps({"name": plugin, "version": version}, indent=2) + "\n", encoding="utf-8"
    )


def _write_marketplace(repo: Path, plugins: dict[str, str]) -> None:
    mkt_dir = repo / ".github" / "plugin"
    mkt_dir.mkdir(parents=True, exist_ok=True)
    entries = [
        {"name": name, "description": "test", "version": version, "source": f"plugins/{name}"}
        for name, version in plugins.items()
    ]
    payload = {
        "name": "copilot-extensions",
        "metadata": {"description": "test", "version": "1.0.0-dev1"},
        "plugins": entries,
    }
    (mkt_dir / "marketplace.json").write_text(json.dumps(payload, indent=2) + "\n",
                                                encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A minimal, real git repo with a copy of the real tooling modules, one
    plugin, and a marketplace.json -- enough for promote_release.py to run
    against unmodified."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init", "-q", "-b", "main"], root)
    (root / "tools").mkdir()
    for name in _REQUIRED_TOOLS:
        shutil.copy(_TOOLS / name, root / "tools" / name)
    _write_plugin(root, "demo-plugin", "0.1.0-dev1")
    _write_marketplace(root, {"demo-plugin": "0.1.0-dev1"})
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _commit(root, "initial")
    _git(["branch", "dev"], root)
    return root


def test_promote_reports_no_change_when_dev_matches_main(repo: Path):
    report = pr.promote(repo=repo, dev_ref="dev", main_ref="main", push=False)
    assert report["promoted"] is False
    assert "no content change" in report["reason"]


def test_promote_creates_wholesale_replace_commit(repo: Path):
    _git(["checkout", "-q", "dev"], repo)
    changefile_dir = repo / ".changefiles"
    changefile_dir.mkdir()
    (changefile_dir / "20260101-test-abc123.json").write_text(
        json.dumps({"comment": "test change", "changes": [{"plugin": "demo-plugin", "type": "patch"}]}),
        encoding="utf-8",
    )
    (repo / "plugins" / "demo-plugin" / "new-file.txt").write_text("content\n", encoding="utf-8")
    dev_head = _commit(repo, "demo-plugin: add a file + changefile")
    _git(["checkout", "-q", "main"], repo)

    main_before = _git(["rev-parse", "main"], repo)
    report = pr.promote(repo=repo, dev_ref="dev", main_ref="main", push=False)

    assert report["promoted"] is True
    assert report["main_before"] == main_before
    assert report["dev_head"] == dev_head
    assert report["bumps"] == {"demo-plugin": ("0.1.0-dev1", "0.1.1-dev1")}
    assert len(report["changefiles_consumed"]) == 1
    assert report["pushed"] is False

    commit = report["commit"]
    parents = _git(["log", "-1", "--format=%P", commit], repo).split()
    assert parents == [main_before]

    plugin_json = _git(["show", f"{commit}:plugins/demo-plugin/plugin.json"], repo)
    assert json.loads(plugin_json)["version"] == "0.1.1-dev1"
    new_file = _git(["show", f"{commit}:plugins/demo-plugin/new-file.txt"], repo)
    assert new_file == "content"

    # The changefile must not exist in the generated commit's tree.
    listing = _git(["ls-tree", "-r", "--name-only", commit], repo)
    assert ".changefiles" not in listing

    # Tag was created and points at the generated commit.
    tags = _git(["tag", "--points-at", commit], repo)
    assert report["tag"] in tags.splitlines()

    # main's real ref was NOT moved (push=False).
    assert _git(["rev-parse", "main"], repo) == main_before

    # No leftover scratch worktrees.
    worktrees = _git(["worktree", "list"], repo)
    assert worktrees.count("\n") == 0 or len(worktrees.splitlines()) == 1


def test_promote_push_moves_main_and_pushes_tag(tmp_path: Path, repo: Path):
    origin = tmp_path / "origin.git"
    _git(["init", "-q", "--bare", str(origin)], tmp_path)
    _git(["remote", "add", "origin", str(origin)], repo)
    _git(["push", "-q", "origin", "main"], repo)
    _git(["push", "-q", "origin", "dev"], repo)

    _git(["checkout", "-q", "dev"], repo)
    (repo / "plugins" / "demo-plugin" / "another-file.txt").write_text("x\n", encoding="utf-8")
    _commit(repo, "demo-plugin: another change (no changefile)")
    _git(["push", "-q", "origin", "dev"], repo)
    _git(["checkout", "-q", "main"], repo)
    _git(["fetch", "-q", "origin"], repo)

    report = pr.promote(repo=repo, dev_ref="origin/dev", main_ref="origin/main", push=True)

    assert report["promoted"] is True
    assert report["pushed"] is True
    remote_main = _git(["ls-remote", str(origin), "refs/heads/main"], repo).split()[0]
    assert remote_main == report["commit"]
    remote_tags = _git(["ls-remote", "--tags", str(origin)], repo)
    assert report["tag"] in remote_tags


def test_promote_with_candidate_branch_never_touches_main_directly(tmp_path: Path, repo: Path):
    """A protected main (personal-account rulesets have no Integration
    bypass -- see the effort's Journal) must never receive a direct push;
    promote() with candidate_branch pushes elsewhere and leaves main/the
    tag for the caller to land via a real PR + merge."""
    origin = tmp_path / "origin.git"
    _git(["init", "-q", "--bare", str(origin)], tmp_path)
    _git(["remote", "add", "origin", str(origin)], repo)
    _git(["push", "-q", "origin", "main"], repo)
    _git(["push", "-q", "origin", "dev"], repo)

    _git(["checkout", "-q", "dev"], repo)
    (repo / "plugins" / "demo-plugin" / "another-file.txt").write_text("x\n", encoding="utf-8")
    _commit(repo, "demo-plugin: another change (no changefile)")
    _git(["push", "-q", "origin", "dev"], repo)
    _git(["checkout", "-q", "main"], repo)
    _git(["fetch", "-q", "origin"], repo)
    main_before = _git(["rev-parse", "origin/main"], repo)

    report = pr.promote(
        repo=repo, dev_ref="origin/dev", main_ref="origin/main",
        push=True, candidate_branch="release/promote-test",
    )

    assert report["promoted"] is True
    assert report["candidate_branch"] == "release/promote-test"
    # main itself was never touched.
    assert _git(["ls-remote", str(origin), "refs/heads/main"], repo).split()[0] == main_before
    # the candidate branch carries the generated commit.
    remote_candidate = _git(
        ["ls-remote", str(origin), "refs/heads/release/promote-test"], repo
    ).split()[0]
    assert remote_candidate == report["commit"]
    # no tag pushed yet -- the caller tags the real post-merge commit.
    remote_tags = _git(["ls-remote", "--tags", str(origin)], repo)
    assert report["tag"] not in remote_tags


def test_promote_message_lists_bumps_and_changefiles():
    summary = {
        "bumps": {"demo-plugin": ("0.1.0-dev1", "0.1.0-dev2")},
        "changefiles_consumed": ["20260101-test-abc123.json"],
    }
    message = pr.format_promotion_message(dev_range=("aaaa" * 10, "bbbb" * 10), summary=summary)
    assert "demo-plugin: 0.1.0-dev1 -> 0.1.0-dev2" in message


def test_promote_writes_pipeline_state_into_generated_commit(repo: Path):
    _git(["checkout", "-q", "dev"], repo)
    (repo / "plugins" / "demo-plugin" / "new-file.txt").write_text("x\n", encoding="utf-8")
    dev_head = _commit(repo, "demo-plugin: a change")
    _git(["checkout", "-q", "main"], repo)

    report = pr.promote(repo=repo, dev_ref="dev", main_ref="main", push=False)
    assert report["promoted"] is True

    state_json = _git(["show", f"{report['commit']}:{pr.PIPELINE_STATE_PATH}"], repo)
    state = json.loads(state_json)
    assert state["last_promotion"]["dev_head"] == dev_head
    assert report["tag"].startswith(state["last_promotion"]["tag"])
    assert state["paused"] is False


def test_promote_a_second_time_with_only_state_change_is_a_no_op(repo: Path):
    """Re-running promote() against the SAME dev content a second time must
    not treat the previous commit's own state-file update as a real content
    change -- otherwise every promotion would spuriously "change" forever."""
    _git(["checkout", "-q", "dev"], repo)
    (repo / "plugins" / "demo-plugin" / "new-file.txt").write_text("x\n", encoding="utf-8")
    _commit(repo, "demo-plugin: a change")
    _git(["checkout", "-q", "main"], repo)

    first = pr.promote(repo=repo, dev_ref="dev", main_ref="main", push=False)
    assert first["promoted"] is True
    _git(["update-ref", "refs/heads/main", first["commit"]], repo)

    second = pr.promote(repo=repo, dev_ref="dev", main_ref="main", push=False)
    assert second["promoted"] is False


def test_promote_refuses_when_paused(repo: Path):
    _git(["checkout", "-q", "dev"], repo)
    (repo / "plugins" / "demo-plugin" / "new-file.txt").write_text("x\n", encoding="utf-8")
    _commit(repo, "demo-plugin: a change")
    _git(["checkout", "-q", "main"], repo)

    (repo / ".github").mkdir(exist_ok=True)
    (repo / pr.PIPELINE_STATE_PATH).write_text(
        json.dumps({"paused": True, "pause_reason": "investigating a bad release"}),
        encoding="utf-8",
    )
    _commit(repo, "release-pipeline: pause promotion")

    with pytest.raises(pr.PromotionPaused, match="investigating a bad release"):
        pr.promote(repo=repo, dev_ref="dev", main_ref="main", push=False)


def test_main_cli_exits_zero_on_pause_not_one(repo: Path, capsys):
    (repo / ".github").mkdir(exist_ok=True)
    (repo / pr.PIPELINE_STATE_PATH).write_text(
        json.dumps({"paused": True, "pause_reason": "x"}), encoding="utf-8"
    )
    _commit(repo, "release-pipeline: pause promotion")
    code = pr.main(["--repo", str(repo), "--dev-ref", "dev", "--main-ref", "main"])
    assert code == 0
    assert "paused" in capsys.readouterr().out.lower()


def test_promote_refuses_to_repromote_a_rolled_back_dev_state(repo: Path):
    _git(["checkout", "-q", "dev"], repo)
    (repo / "plugins" / "demo-plugin" / "new-file.txt").write_text("bad\n", encoding="utf-8")
    dev_head = _commit(repo, "demo-plugin: a bad change")
    _git(["checkout", "-q", "main"], repo)

    (repo / ".github").mkdir(exist_ok=True)
    (repo / pr.PIPELINE_STATE_PATH).write_text(
        json.dumps({
            "paused": False,
            "last_rollback": {
                "reverted_dev_head": dev_head,
                "reason": "broke something",
            },
        }),
        encoding="utf-8",
    )
    _commit(repo, "release-pipeline: record rollback")

    with pytest.raises(pr.NonIncrementalPromotion, match="broke something"):
        pr.promote(repo=repo, dev_ref="dev", main_ref="main", push=False)

    # --force overrides the guard deliberately.
    report = pr.promote(repo=repo, dev_ref="dev", main_ref="main", push=False, force=True)
    assert report["promoted"] is True


def test_promote_no_changefiles_reports_none():
    summary = {"bumps": {}, "changefiles_consumed": []}
    message = pr.format_promotion_message(dev_range=("a" * 40, "b" * 40), summary=summary)
    assert "Version bumps: none" in message
    assert "Changefiles consumed: none" in message
