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


def test_promote_refuses_when_a_pointer_does_not_resolve(repo: Path):
    # A malformed/dangling VENDOR_POINTER.json on dev (pointing at a lib
    # that doesn't exist in canonical libs/) must abort promotion, not ship
    # an unexpanded stub into main.
    _git(["checkout", "-q", "dev"], repo)
    pointer_dir = repo / "plugins" / "demo-plugin" / "libs" / "ghost-lib"
    pointer_dir.mkdir(parents=True)
    (pointer_dir / "VENDOR_POINTER.json").write_text(
        json.dumps({"schema": "copilot-extensions.vendor-pointer", "version": 1,
                    "source": "libs/ghost-lib"}) + "\n",
        encoding="utf-8",
    )
    _commit(repo, "demo-plugin: add a dangling vendor pointer")
    _git(["checkout", "-q", "main"], repo)
    main_before = _git(["rev-parse", "main"], repo)

    with pytest.raises(pr.PromotionError, match="did not resolve"):
        pr.promote(repo=repo, dev_ref="dev", main_ref="main", push=False)

    # main's ref was not moved, and no leftover scratch worktree remains.
    assert _git(["rev-parse", "main"], repo) == main_before
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


def _write_changefile(repo: Path, name: str, plugin: str, bump_type: str) -> None:
    cf_dir = repo / ".changefiles"
    cf_dir.mkdir(exist_ok=True)
    (cf_dir / name).write_text(
        json.dumps({"comment": "test", "changes": [{"plugin": plugin, "type": bump_type}]}),
        encoding="utf-8",
    )


def test_promote_does_not_reapply_a_changefile_left_behind_on_dev(repo: Path):
    """Regression: a promotion never mutates dev's own tree (by design), so
    an already-consumed changefile can keep sitting there indefinitely (the
    async dev-side cleanup PR is best-effort, not synchronous). Confirmed
    live: two consecutive real promotions both recomputed the identical
    bump target from dev's still-unbumped plugin.json. A later promotion
    must sweep every changefile currently present and only apply ones that
    did NOT already exist in dev at the previous promotion's own dev_head --
    never a persisted "have we ever seen this filename" list."""
    _git(["checkout", "-q", "dev"], repo)
    _write_changefile(repo, "20260101-first-abc123.json", "demo-plugin", "patch")
    (repo / "plugins" / "demo-plugin" / "new-file.txt").write_text("x\n", encoding="utf-8")
    _commit(repo, "demo-plugin: first change + changefile")
    _git(["checkout", "-q", "main"], repo)

    first = pr.promote(repo=repo, dev_ref="dev", main_ref="main", push=False)
    assert first["promoted"] is True
    assert first["bumps"] == {"demo-plugin": ("0.1.0-dev1", "0.1.1-dev1")}
    assert first["changefiles_consumed"] == ["20260101-first-abc123.json"]
    assert first["changefiles_newly_applied"] == ["20260101-first-abc123.json"]
    _git(["update-ref", "refs/heads/main", first["commit"]], repo)
    _git(["reset", "--hard", "main"], repo)

    # The changefile is STILL physically present on dev (never deleted by
    # promote itself) -- add unrelated new content but no new changefile.
    _git(["checkout", "-q", "dev"], repo)
    (repo / "plugins" / "demo-plugin" / "another-file.txt").write_text("y\n", encoding="utf-8")
    _commit(repo, "demo-plugin: unrelated change, leftover changefile still present")
    _git(["checkout", "-q", "main"], repo)

    second = pr.promote(repo=repo, dev_ref="dev", main_ref="main", push=False)
    assert second["promoted"] is True
    # The leftover changefile must NOT drive a second, redundant bump.
    assert second["bumps"] == {}
    assert second["changefiles_newly_applied"] == []
    # It is still reported as (still) consumed, for the async dev-cleanup
    # step to eventually delete -- just not re-applied to a version bump.
    assert second["changefiles_consumed"] == ["20260101-first-abc123.json"]
    _git(["update-ref", "refs/heads/main", second["commit"]], repo)
    _git(["reset", "--hard", "main"], repo)

    # A genuinely NEW changefile, landing while the old one is still present
    # (dev cleanup hasn't happened yet) -- only the new one may drive a bump.
    _git(["checkout", "-q", "dev"], repo)
    _write_changefile(repo, "20260102-second-def456.json", "demo-plugin", "dev")
    (repo / "plugins" / "demo-plugin" / "third-file.txt").write_text("z\n", encoding="utf-8")
    _commit(repo, "demo-plugin: second real change + new changefile")
    _git(["checkout", "-q", "main"], repo)

    third = pr.promote(repo=repo, dev_ref="dev", main_ref="main", push=False)
    assert third["promoted"] is True
    # Continues from main's actual last-shipped "0.1.1-dev1" (this test's
    # seed step in action), never regressing to dev's frozen "0.1.0-dev1" --
    # the important assertion is that only ONE new changefile drove it.
    assert third["bumps"] == {"demo-plugin": ("0.1.1-dev1", "0.1.1-dev2")}
    assert third["changefiles_newly_applied"] == ["20260102-second-def456.json"]
    assert sorted(third["changefiles_consumed"]) == [
        "20260101-first-abc123.json", "20260102-second-def456.json",
    ]


def test_promote_preserves_shipped_version_when_no_new_changefile_at_all(repo: Path):
    """The exact regression this fix corrects: a promotion round with ZERO
    new changefiles (an already-consumed one is still physically present,
    but nothing genuinely new landed) must still carry main's actual
    last-shipped version forward into the new snapshot -- never fall back
    to dev's own frozen plugin.json literal. Confirmed live: the first
    promotion after the "stop re-applying an already-consumed changefile"
    fix landed regressed 11 of 13 real plugins on main this exact way."""
    _git(["checkout", "-q", "dev"], repo)
    _write_changefile(repo, "20260101-abc123.json", "demo-plugin", "patch")
    (repo / "plugins" / "demo-plugin" / "new-file.txt").write_text("x\n", encoding="utf-8")
    _commit(repo, "demo-plugin: change + changefile")
    _git(["checkout", "-q", "main"], repo)

    first = pr.promote(repo=repo, dev_ref="dev", main_ref="main", push=False)
    assert first["bumps"] == {"demo-plugin": ("0.1.0-dev1", "0.1.1-dev1")}
    _git(["update-ref", "refs/heads/main", first["commit"]], repo)
    _git(["reset", "--hard", "main"], repo)

    # Unrelated new content on dev; the old changefile is still present but
    # was already consumed, so this round drives NO new bump at all.
    _git(["checkout", "-q", "dev"], repo)
    (repo / "plugins" / "demo-plugin" / "another-file.txt").write_text("y\n", encoding="utf-8")
    _commit(repo, "demo-plugin: unrelated change only")
    _git(["checkout", "-q", "main"], repo)

    second = pr.promote(repo=repo, dev_ref="dev", main_ref="main", push=False)
    assert second["promoted"] is True
    assert second["bumps"] == {}

    # The generated commit's own tree must still carry the SHIPPED
    # "0.1.1-dev1" -- never dev's frozen "0.1.0-dev1".
    plugin_json = _git(["show", f"{second['commit']}:plugins/demo-plugin/plugin.json"], repo)
    assert json.loads(plugin_json)["version"] == "0.1.1-dev1"
    marketplace_json = json.loads(
        _git(["show", f"{second['commit']}:.github/plugin/marketplace.json"], repo)
    )
    entry = next(p for p in marketplace_json["plugins"] if p["name"] == "demo-plugin")
    assert entry["version"] == "0.1.1-dev1"


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


def _copy_customizing_copilot_scripts(repo: Path) -> None:
    src = (
        _TOOLS.parent / "plugins" / "customizing-copilot" / "skills"
        / "reviewing-customizations" / "scripts"
    )
    dest = (
        repo / "plugins" / "customizing-copilot" / "skills"
        / "reviewing-customizations" / "scripts"
    )
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.is_file():
            shutil.copy(item, dest / item.name)


def _write_projection_plugin(repo: Path, plugin: str, version: str, tagged_version: str) -> None:
    """A minimal plugin that ships a real instruction-projections.json
    declaration + template, mirroring plugins/efforts's real shape -- its
    template embeds ``[owner: <plugin>@<tagged_version>]``, independently of
    ``version`` (the live plugin.json version), so a test can create a
    real, pre-existing drift between the two the way a real un-synced
    version bump does."""
    _write_plugin(repo, plugin, version)
    plugin_dir = repo / "plugins" / plugin
    (plugin_dir / "instructions").mkdir(parents=True, exist_ok=True)
    (plugin_dir / "instruction-projections.json").write_text(
        json.dumps({
            "schema": "copilot-extensions.instruction-projections",
            "version": 1,
            "projections": [{
                "id": "demo-note",
                "template": "instructions/demo-note.instructions.md",
                "destination": f".github/instructions/{plugin}/demo-note.instructions.md",
                "customizationKind": "instructions",
                "applyTo": "**",
                "legacyMarkers": [],
            }],
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    (plugin_dir / "instructions" / "demo-note.instructions.md").write_text(
        '---\napplyTo: "**"\n---\n\n'
        f"# Demo note\n\nFallback policy `[owner: {plugin}@{tagged_version}]`: static content.\n",
        encoding="utf-8",
    )


def _write_enabled_settings(repo: Path, plugin: str) -> None:
    settings_dir = repo / ".github" / "copilot"
    settings_dir.mkdir(parents=True, exist_ok=True)
    (settings_dir / "settings.json").write_text(
        json.dumps({"enabledPlugins": {f"{plugin}@copilot-extensions": True}}) + "\n",
        encoding="utf-8",
    )


def test_promote_syncs_instruction_projections_after_a_version_bump(tmp_path: Path):
    """Regression for the #3378-recurrence found live on `main` 2026-09-25:
    a plugin's version bump must also re-render any instruction-projection
    file it owns (its rendered content embeds the plugin's version
    verbatim), or the projected file silently drifts from the version the
    very next promotion actually ships -- exactly what happened for real
    (plugins/efforts bumped to 0.1.1-dev1 while its checked-in
    .github/instructions/efforts/completion-gate.instructions.md still
    read 0.1.0-dev22)."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init", "-q", "-b", "main"], root)
    (root / "tools").mkdir()
    for name in _REQUIRED_TOOLS:
        shutil.copy(_TOOLS / name, root / "tools" / name)

    plugin = "demo-plugin-proj"
    _write_projection_plugin(root, plugin, "0.1.0-dev1", "0.1.0-dev1")
    _write_marketplace(root, {plugin: "0.1.0-dev1"})
    _write_enabled_settings(root, plugin)
    _copy_customizing_copilot_scripts(root)
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _commit(root, "initial")
    _git(["branch", "dev"], root)

    _git(["checkout", "-q", "dev"], root)
    _write_changefile(root, "20260101-bump-abc123.json", plugin, "patch")
    _commit(root, "add changefile")
    _git(["checkout", "-q", "main"], root)

    report = pr.promote(repo=root, dev_ref="dev", main_ref="main", push=False)

    assert report["promoted"] is True
    assert report["bumps"] == {plugin: ("0.1.0-dev1", "0.1.1-dev1")}
    destination = f".github/instructions/{plugin}/demo-note.instructions.md"
    assert destination in report["projections_synced"]

    commit = report["commit"]
    rendered = _git(["show", f"{commit}:{destination}"], root)
    assert f"[owner: {plugin}@0.1.1-dev1]" in rendered
    assert f"[owner: {plugin}@0.1.0-dev1]" not in rendered


def test_promote_projection_sync_is_a_noop_without_customizing_copilot(repo: Path):
    """The sweep is best-effort, never a hard dependency: a tree with no
    plugins/customizing-copilot present at all (this shared fixture, and
    every other test in this file) must promote exactly as before."""
    _git(["checkout", "-q", "dev"], repo)
    _write_changefile(repo, "20260101-test-abc123.json", "demo-plugin", "patch")
    _commit(repo, "add changefile")
    _git(["checkout", "-q", "main"], repo)

    report = pr.promote(repo=repo, dev_ref="dev", main_ref="main", push=False)

    assert report["promoted"] is True
    assert report["projections_synced"] == []
