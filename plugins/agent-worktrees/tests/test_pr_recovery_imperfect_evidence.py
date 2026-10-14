"""Imperfect optional settlement evidence never invents or blocks rewrite consent."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_worktrees import finalize, git_ops, pr_recovery
from test_pr_recovery_advanced_head import REASON, PrRepo, backed_publication, git, metadata, preview, record

pytestmark = [pytest.mark.timeout(120)]


@pytest.mark.parametrize("defect", [
    "json", "schema", "fields", "dangling", "unreadable",
    "null-ref", "surrogate-ref", "spawn", "synced-encoding",
])
def test_unusable_checkpoint_produces_bound_rewrite_preview_and_confirmed_rewrite(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str],
    defect: str,
) -> None:
    config, wid, wt, remote, saved, branch, published = backed_publication(pr_repo, monkeypatch)
    checkpoint = pr_recovery._path(str(wt))
    read_original_bytes = Path.read_bytes
    original = checkpoint.read_bytes()
    collection = json.loads(original)
    point = collection["checkpoints"][collection["latest"]]
    if defect == "json":
        checkpoint.write_text("{unfinished checkpoint")
    elif defect == "schema":
        collection["version"] = 999
        checkpoint.write_text(json.dumps(collection))
    elif defect == "fields":
        point["local_ref"] = 42
        checkpoint.write_text(json.dumps(collection))
    elif defect == "dangling":
        git("update-ref", "-d", point["local_ref"], point["local_head"], cwd=wt)
    elif defect in {"null-ref", "surrogate-ref"}:
        point["local_ref"] = "\0" if defect == "null-ref" else "\ud800"
        checkpoint.write_text(json.dumps(collection))
    elif defect in {"spawn", "synced-encoding"}:
        real_git = git_ops.git

        def probe(*args: str, **kwargs: object) -> object:
            if "--end-of-options" in args and args[-1].startswith(point["local_ref"]):
                if defect == "spawn":
                    raise OSError(22, "evidence probe could not start")
                if args[-1] == point["local_ref"] + "^{commit}":
                    raise UnicodeEncodeError("utf-8", "\ud800", 0, 1, "unencodable evidence")
            return real_git(*args, **kwargs)

        monkeypatch.setattr(git_ops, "git", probe)
    else:
        real_text, real_bytes = Path.read_text, Path.read_bytes

        def read_text(path: Path, *args: object, **kwargs: object) -> str:
            if path == checkpoint:
                raise PermissionError(13, "checkpoint is unreadable")
            return real_text(path, *args, **kwargs)

        def read_bytes(path: Path) -> bytes:
            if path == checkpoint:
                raise PermissionError(13, "checkpoint is unreadable")
            return real_bytes(path)

        monkeypatch.setattr(Path, "read_text", read_text)
        monkeypatch.setattr(Path, "read_bytes", read_bytes)
    evidence_bytes = read_original_bytes(checkpoint)
    capfd.readouterr()
    first = preview(wid, config)
    operation = first["operation"]
    assert operation["action"] == "rewrite"
    assert operation["evidence_status"] == "unusable"
    assert operation["evidence_fingerprint"]
    assert operation["expected_head"] == published
    assert operation["saved_head"] == saved
    captured = capfd.readouterr()
    assert "settlement evidence is unusable" in captured.out + captured.err
    assert not list(metadata(wt).glob("*break-glass*.json"))
    assert record(wid).pr.head_sha == saved
    assert git("--git-dir", str(remote), "rev-parse", branch, cwd=wt) == published
    assert finalize.push_changes(
        wid, config, break_glass_reason=REASON, confirm_break_glass=first["confirmation"],
    )
    assert record(wid).pr.head_sha == git("rev-parse", "HEAD", cwd=wt)
    audit = json.loads(next(metadata(wt).glob("*break-glass*.json")).read_text())
    assert audit["operation"]["evidence_status"] == "unusable"
    assert audit["operation"]["action"] == "rewrite"
    assert audit["saved_publication"]["head_sha"] == saved
    assert git("rev-parse", audit["recovery_refs"]["published"], cwd=wt) == published
    assert read_original_bytes(checkpoint) == evidence_bytes


@pytest.mark.parametrize("change", ["broken-bytes", "repaired-proof", "other-retained-ref"])
def test_changed_imperfect_evidence_invalidates_confirmation(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    config, wid, wt, _remote, _saved, _branch, _published = backed_publication(pr_repo, monkeypatch)
    checkpoint = pr_recovery._path(str(wt))
    original = checkpoint.read_bytes()
    point = json.loads(original)
    point = point["checkpoints"][point["latest"]]
    if change == "other-retained-ref":
        git("update-ref", "-d", point["local_ref"], point["local_head"], cwd=wt)
    else:
        checkpoint.write_text("{broken proof")
    first = preview(wid, config)
    if change == "broken-bytes":
        checkpoint.write_text("{different broken proof")
    elif change == "repaired-proof":
        checkpoint.write_bytes(original)
    else:
        # The first missing ref still raises; another reference changes too.
        git("update-ref", point["published_ref"], point["synced_head"], point["published_head"], cwd=wt)
    changed = preview(wid, config, first["confirmation"])
    assert changed["confirmation"] != first["confirmation"]
    assert not list(metadata(wt).glob("*break-glass*.json"))


@pytest.mark.parametrize("kind", ["blob", "tree", "tag"])
def test_recreated_non_commit_evidence_ref_requires_fresh_confirmation(
    pr_repo: PrRepo, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    config, wid, wt, _remote, saved, _branch, _published = backed_publication(pr_repo, monkeypatch)
    collection = json.loads(pr_recovery._path(str(wt)).read_bytes())
    point = collection["checkpoints"][collection["latest"]]
    git("update-ref", "-d", point["local_ref"], point["local_head"], cwd=wt)
    first = preview(wid, config)
    blob = git("hash-object", "-w", str(wt / "unpublished.txt"), cwd=wt)
    if kind == "tree":
        object_id = git("rev-parse", "HEAD^{tree}", cwd=wt)
    elif kind == "tag":
        git("tag", "-a", "non-commit-evidence", "-m", "blob evidence", blob, cwd=wt)
        object_id = git("rev-parse", "refs/tags/non-commit-evidence", cwd=wt)
    else:
        object_id = blob
    git("update-ref", point["local_ref"], object_id, cwd=wt)
    changed = preview(wid, config, first["confirmation"])
    assert changed["confirmation"] != first["confirmation"]
    assert changed["operation"]["evidence_status"] == "unusable"
    assert record(wid).pr.head_sha == saved
    assert not list(metadata(wt).glob("*break-glass*.json"))
