"""Real-Git and negative contract coverage for inert control preservation."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import promotion_control as control
import promote_release as promotion
import test_promote_release as fixtures
from test_promote_release import _commit, _git


WORKFLOW = ".github/workflows/control-test.yml"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return fixtures.repo.__wrapped__(tmp_path)


def _manifest(files=None, **extra) -> bytes:
    return json.dumps({
        "schema": control.SCHEMA, "version": 1,
        "files": [WORKFLOW] if files is None else files, **extra,
    }).encode()


def _enable(repo: Path, *, raw: bytes | None = None) -> str:
    path = repo / WORKFLOW
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"name: trusted\r\non: workflow_dispatch\r\n")
    (repo / control.MANIFEST_PATH).write_bytes(_manifest() if raw is None else raw)
    return _commit(repo, "main: declare test control")


def test_preservation_uses_pinned_main_not_live_main_or_dev(repo: Path):
    pinned = _enable(repo)
    expected = control._git(repo, ["show", f"{pinned}:{WORKFLOW}"])
    (repo / WORKFLOW).write_text("name: later-main\n", encoding="utf-8")
    _commit(repo, "main: later control repair")
    _git(["checkout", "-q", "dev"], repo)
    (repo / WORKFLOW).parent.mkdir(parents=True, exist_ok=True)
    (repo / WORKFLOW).write_text("name: dev-only\n", encoding="utf-8")
    (repo / "README.md").write_text("new dev data\n", encoding="utf-8")
    _commit(repo, "dev: content and competing workflow")
    report = promotion.promote(repo=repo, dev_ref="dev", main_ref=pinned, push=False)
    actual = control._git(repo, ["show", f"{report['commit']}:{WORKFLOW}"])
    assert actual == expected
    assert _git(["show", f"{report['commit']}:README.md"], repo) == "new dev data"
    provenance = report["control_preserved"]
    assert provenance["main_head"] == pinned
    assert set(provenance["files"]) == {WORKFLOW, control.MANIFEST_PATH}
    state = json.loads(_git(["show", f"{report['commit']}:{promotion.PIPELINE_STATE_PATH}"], repo))
    assert state["last_promotion"]["control_preserved"] == provenance
    assert _git(["rev-parse", "main"], repo) != pinned


def test_manifest_absence_preserves_legacy_report_and_tree(repo: Path):
    report = promotion.promote(repo=repo, dev_ref="dev", main_ref="main", push=False)
    assert not report["promoted"]
    assert "control_preserved" not in report


def test_dev_cannot_self_activate(repo: Path):
    _git(["checkout", "-q", "dev"], repo)
    _enable(repo)
    with pytest.raises(promotion.PromotionError, match="dev cannot introduce"):
        promotion.promote(repo=repo, dev_ref="dev", main_ref="main", push=False)


def test_generated_untracked_manifest_cannot_self_activate(repo: Path):
    pinned = _git(["rev-parse", "main"], repo)
    scratch = promotion.add_scratch_worktree("dev", repo=repo)
    try:
        (scratch / ".github" / "promotion-control.json").write_bytes(_manifest())
        with pytest.raises(control.ControlPreservationError, match="dev cannot introduce"):
            control.preserve_control(scratch, main_head=pinned, repo=repo)
    finally:
        promotion.remove_scratch_worktree(scratch, repo=repo)


def test_dev_cannot_redefine_main_manifest(repo: Path):
    _enable(repo)
    _git(["checkout", "-q", "dev"], repo)
    (repo / ".github").mkdir(exist_ok=True)
    (repo / control.MANIFEST_PATH).write_bytes(_manifest([".github/workflows/other.yml"]))
    _commit(repo, "dev: conflicting declaration")
    with pytest.raises(promotion.PromotionError, match="conflicting control declaration"):
        promotion.promote(repo=repo, dev_ref="dev", main_ref="main", push=False)


@pytest.mark.parametrize("files", [
    [], [WORKFLOW, WORKFLOW], ["tools/helper.py"], [".github/CODEOWNERS"],
    [".github/workflows/../tools.yml"], [".github/workflows/nested/control.yml"],
    [".github/workflows/Control.yml"], [".github/workflows/con.yml"],
    [".github/workflows/aux.test.yml"], ["/absolute.yml"], ["C:\\control.yml"],
    [".github\\workflows\\control.yml"], ["*.yml"], [None],
])
def test_manifest_rejects_nonportable_or_noncontrol_paths(files):
    with pytest.raises(control.ControlPreservationError):
        control._paths(_manifest(files))


@pytest.mark.parametrize("raw", [
    b"{", b"\xff", b"[]",
    _manifest(version=True), _manifest(version=2), _manifest(schema="other"),
    _manifest(extra="unreviewed"), _manifest(files="not-list"),
    b'{"schema":"a","schema":"b","version":1,"files":[]}',
])
def test_manifest_rejects_malformed_or_ambiguous_schema(raw):
    with pytest.raises(control.ControlPreservationError):
        control._paths(raw)


def test_missing_declared_main_file_fails_before_candidate_mutation(repo: Path):
    pinned = _enable(repo, raw=_manifest([".github/workflows/missing.yml"]))
    scratch = promotion.add_scratch_worktree("dev", repo=repo)
    try:
        with pytest.raises(control.ControlPreservationError, match="missing declared"):
            control.preserve_control(scratch, main_head=pinned, repo=repo)
        assert not (scratch / control.MANIFEST_PATH).exists()
    finally:
        promotion.remove_scratch_worktree(scratch, repo=repo)


@pytest.mark.parametrize("mode", ["100755", "120000", "160000"])
def test_unsafe_main_git_mode_is_rejected(repo: Path, mode: str):
    _enable(repo)
    blob = _git(["rev-parse", f"HEAD:{WORKFLOW}"], repo) if mode != "160000" else _git(["rev-parse", "HEAD"], repo)
    _git(["update-index", "--cacheinfo", f"{mode},{blob},{WORKFLOW}"], repo)
    tree = _git(["write-tree"], repo)
    pinned = _git(["commit-tree", tree, "-p", "HEAD", "-m", "unsafe main entry"], repo)
    scratch = promotion.add_scratch_worktree("dev", repo=repo)
    try:
        with pytest.raises(control.ControlPreservationError, match="regular Git blobs"):
            control.preserve_control(scratch, main_head=pinned, repo=repo)
    finally:
        promotion.remove_scratch_worktree(scratch, repo=repo)


def test_case_alias_in_main_parent_is_rejected(repo: Path):
    _enable(repo)
    blob = _git(["rev-parse", f"HEAD:{WORKFLOW}"], repo)
    _git(["update-index", "--add", "--cacheinfo",
          f"100644,{blob},.github/Workflows/other.yml"], repo)
    tree = _git(["write-tree"], repo)
    pinned = _git(["commit-tree", tree, "-p", "HEAD", "-m", "case alias"], repo)
    scratch = promotion.add_scratch_worktree("dev", repo=repo)
    try:
        with pytest.raises(control.ControlPreservationError, match="case-equivalent main"):
            control.preserve_control(scratch, main_head=pinned, repo=repo)
    finally:
        promotion.remove_scratch_worktree(scratch, repo=repo)


@pytest.mark.parametrize("shape", ["directory", "case-alias", "submodule", "symlink"])
def test_unsafe_candidate_destination_is_rejected(repo: Path, shape: str):
    pinned = _enable(repo)
    scratch = promotion.add_scratch_worktree("dev", repo=repo)
    try:
        if shape == "directory":
            (scratch / WORKFLOW).mkdir(parents=True)
        elif shape == "case-alias":
            (scratch / ".github" / "Workflows").mkdir(parents=True)
        elif shape == "submodule":
            (scratch / ".github" / "workflows").mkdir(parents=True)
            _git(["update-index", "--add", "--cacheinfo",
                  f"160000,{pinned},.github/workflows"], scratch)
        else:
            blob = _git(["rev-parse", f"{pinned}:{WORKFLOW}"], repo)
            _git(["update-index", "--add", "--cacheinfo",
                  f"120000,{blob},.github/workflows"], scratch)
        with pytest.raises(control.ControlPreservationError, match="unsafe|case-equivalent|submodule"):
            control.preserve_control(scratch, main_head=pinned, repo=repo)
    finally:
        promotion.remove_scratch_worktree(scratch, repo=repo)


def test_explicit_retirement_returns_path_to_dev_composition(repo: Path):
    _enable(repo)
    retained = ".github/workflows/control-retained.yml"
    (repo / retained).write_text("name: retained\n", encoding="utf-8")
    (repo / control.MANIFEST_PATH).write_bytes(_manifest([retained]))
    pinned = _commit(repo, "main: reviewed retirement of old wrapper")
    _git(["checkout", "-q", "dev"], repo)
    (repo / "README.md").write_text("retirement input\n", encoding="utf-8")
    _commit(repo, "dev: unrelated content")
    report = promotion.promote(repo=repo, dev_ref="dev", main_ref=pinned, push=False)
    listing = _git(["ls-tree", "-r", "--name-only", report["commit"]], repo).splitlines()
    assert WORKFLOW not in listing
    assert retained in listing
    assert WORKFLOW not in report["control_preserved"]["files"]


def test_git_environment_cannot_redirect_control_read(repo: Path, monkeypatch):
    pinned = _enable(repo)
    monkeypatch.setenv("GIT_DIR", str(repo / "not-the-repository"))
    assert control._git(repo, ["rev-parse", pinned]).strip().decode() == pinned
    assert os.environ["GIT_DIR"].endswith("not-the-repository")


def test_git_replace_ref_cannot_change_pinned_control(repo: Path):
    pinned = _enable(repo)
    expected = control._git(repo, ["show", f"{pinned}:{WORKFLOW}"])
    (repo / WORKFLOW).write_text("name: replaced\n", encoding="utf-8")
    replacement = _commit(repo, "main: replacement tree")
    _git(["replace", pinned, replacement], repo)
    scratch = promotion.add_scratch_worktree("dev", repo=repo)
    try:
        control.preserve_control(scratch, main_head=pinned, repo=repo)
        assert (scratch / WORKFLOW).read_bytes() == expected
    finally:
        promotion.remove_scratch_worktree(scratch, repo=repo)


def test_candidate_filters_and_filemode_do_not_change_preserved_blobs(repo: Path):
    (repo / ".gitattributes").write_text("*.yml -text\n", encoding="utf-8")
    pinned = _enable(repo)
    expected = control._git(repo, ["show", f"{pinned}:{WORKFLOW}"])
    assert b"\r\n" in expected
    _git(["checkout", "-q", "dev"], repo)
    (repo / ".gitattributes").write_text("*.yml text eol=lf\n", encoding="utf-8")
    (repo / WORKFLOW).parent.mkdir(parents=True, exist_ok=True)
    (repo / WORKFLOW).write_bytes(b"dev data\n")
    _commit(repo, "dev: different text policy")
    blob = _git(["rev-parse", f"HEAD:{WORKFLOW}"], repo)
    _git(["update-index", "--cacheinfo", f"100755,{blob},{WORKFLOW}"], repo)
    _git(["commit", "-q", "-m", "dev: executable workflow"], repo)
    _git(["config", "core.filemode", "false"], repo)
    report = promotion.promote(repo=repo, dev_ref="dev", main_ref=pinned, push=False)
    assert control._git(repo, ["show", f"{report['commit']}:{WORKFLOW}"]) == expected
    assert _git(["ls-tree", report["commit"], "--", WORKFLOW], repo).startswith("100644 blob ")


def test_trusted_artifact_recomposition_uses_same_control_tree(repo: Path, monkeypatch, tmp_path):
    import promotion_python_artifacts as artifacts

    pinned = _enable(repo)
    _git(["checkout", "-q", "dev"], repo)
    (repo / "README.md").write_text("artifact input\n", encoding="utf-8")
    dev = _commit(repo, "dev: artifact input")
    monkeypatch.setattr(artifacts, "validate_artifacts", lambda directory, **kwargs: kwargs)
    result = artifacts.validate_candidate_artifacts(
        tmp_path, repo=repo, dev_head=dev, main_before=pinned,
    )
    assert result["dev_head"] == dev
    assert result["main_before"] == pinned
    for path in [WORKFLOW, control.MANIFEST_PATH]:
        assert _git(["rev-parse", f"{result['content_tree']}:{path}"], repo) == _git(
            ["rev-parse", f"{pinned}:{path}"], repo,
        )
