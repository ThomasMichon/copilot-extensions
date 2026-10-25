"""Regression tests for the large-file guard (image/default caps + the
always-blocked source-map/diff/patch denylist).

Drives the real ``tools/check-large-files.py`` as a subprocess inside a
throwaway git repo, the same pattern ``test_check_module_size.py`` uses --
with focused in-process controls for Git metadata failures and bounded batch
protocols that a real object store cannot reliably reproduce without corruption.

Run:  python -m pytest tools/test_check_large_files.py
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-large-files.py"


def _load_module():
    """Import check-large-files.py as a module (its hyphenated filename
    isn't a valid Python identifier for a plain ``import``).
    """
    spec = importlib.util.spec_from_file_location("check_large_files", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_new_blob_paths_reads_all_commits_in_one_lossless_batch():
    module = _load_module()
    commits = ["a" * 40, "b" * 40]
    blob = "c" * 40
    names = ["line\nbreak.txt", "line\tbreak.map"]
    raw = b"".join(
        f":000000 100644 {'0' * 40} {blob} A\0{name}\0".encode()
        for name in names
    )
    with mock.patch.object(module, "_git_bytes", return_value=subprocess.CompletedProcess(
        [], 0, raw, b"",
    )) as git:
        assert module._new_blob_paths(commits, {blob: 1}) == [(names[1], 1)]
    assert git.call_count == 1
    assert "--stdin" in git.call_args.args
    assert git.call_args.kwargs["input"] == ("\n".join(commits) + "\n").encode()


def test_new_blob_paths_refuses_failed_batch_or_missing_paths():
    module = _load_module()
    with mock.patch.object(module, "_git_bytes", return_value=subprocess.CompletedProcess(
        [], 1, b"", b"unavailable commit",
    )):
        with pytest.raises(module.GitEnumerationError, match="diff-tree --stdin"):
            module._new_blob_paths(["a" * 40], {"b" * 40: 1})
    with mock.patch.object(module, "_git_bytes", return_value=subprocess.CompletedProcess(
        [], 0, b"", b"",
    )):
        with pytest.raises(module.GitEnumerationError, match="every new blob"):
            module._new_blob_paths(["a" * 40], {"b" * 40: 1})


def test_publication_commit_resolution_batches_and_binds_each_reference():
    module = _load_module()
    raw = f"{'a' * 40} commit 123 HEAD\n{'b' * 40} commit 456 origin/dev\n".encode()
    with mock.patch.object(module, "_git_bytes", return_value=subprocess.CompletedProcess(
        [], 0, raw, b"",
    )) as git:
        assert module._publication_commits(["HEAD", "origin/dev", "HEAD"]) == {
            "HEAD": "a" * 40, "origin/dev": "b" * 40,
        }
    assert git.call_count == 1
    assert git.call_args.kwargs["input"] == b"HEAD^{commit} HEAD\norigin/dev^{commit} origin/dev\n"


@pytest.mark.parametrize("raw", [
    b"", b"not-a-sha commit 1 HEAD\n",
    ("a" * 40 + " blob 1 HEAD\n").encode(),
    ("a" * 40 + " commit invalid HEAD\n").encode(),
    ("a" * 40 + " commit 1 another-ref\n").encode(),
])
def test_publication_commit_resolution_refuses_incomplete_or_mismatched_data(raw):
    module = _load_module()
    with mock.patch.object(module, "_git_bytes", return_value=subprocess.CompletedProcess([], 0, raw, b"")):
        with pytest.raises(module.GitEnumerationError):
            module._publication_commits(["HEAD"])


def test_publication_commit_resolution_retains_missing_ref_and_refuses_batch_injection():
    module = _load_module()
    with mock.patch.object(module, "_git_bytes", return_value=subprocess.CompletedProcess(
        [], 0, b"absent^{commit} missing\n", b"",
    )):
        assert module._publication_commits(["absent"]) == {"absent": None}
    with mock.patch.object(module, "_git_bytes") as git:
        with pytest.raises(module.GitEnumerationError):
            module._publication_commits(["HEAD\nother"])
        git.assert_not_called()


def test_pinned_annotated_tag_keeps_original_identity_separate_from_commit():
    module = _load_module()
    tag, commit = "a" * 40, "b" * 40
    raw = f"{tag} tag 123 {tag}\n{commit} commit 456 {tag}\n".encode()
    with mock.patch.object(module, "_git_bytes", return_value=subprocess.CompletedProcess(
        [], 0, raw, b"",
    )) as git:
        assert module._publication_commits([tag]) == {tag: commit}
    assert git.call_args.kwargs["input"] == f"{tag} {tag}\n{tag}^{{commit}} {tag}\n".encode()


@pytest.mark.parametrize("raw", [
    f"{'b' * 40} tag 123 {'a' * 40}\n{'b' * 40} commit 456 {'a' * 40}\n",
    f"{'a' * 40} commit 123 {'a' * 40}\n{'b' * 40} commit 456 {'a' * 40}\n",
    f"{'a' * 40} missing\n{'b' * 40} commit 456 {'a' * 40}\n",
])
def test_pinned_publication_objects_refuse_identity_or_peeling_mismatch(raw):
    module = _load_module()
    with mock.patch.object(module, "_git_bytes", return_value=subprocess.CompletedProcess(
        [], 0, raw.encode(), b"",
    )):
        with pytest.raises(module.GitEnumerationError):
            module._publication_commits(["a" * 40])


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8")


def _write_bytes(repo: Path, rel: str, size: int) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)


def _stage_blob(repo: Path, name: str, size: int) -> None:
    result = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=repo,
        input=b"x" * size, capture_output=True, check=True,
    )
    blob = result.stdout.decode("ascii").strip()
    subprocess.run(
        ["git", "-c", "core.protectNTFS=false", "update-index", "-z", "--index-info"], cwd=repo,
        input=f"100644 {blob}\t{name}\0".encode("utf-8"),
        capture_output=True, check=True,
    )


def _run(repo: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(repo / "tools" / SCRIPT.name), *extra],
        cwd=repo,
        capture_output=True,
        text=True, encoding="utf-8",
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / SCRIPT.name).write_bytes(SCRIPT.read_bytes())
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    return tmp_path


def _commit_all(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "snapshot")


@pytest.mark.parametrize("size,expected", [(1024, 0), (2 * 1024 * 1024, 1)])
def test_annotated_tag_publication_checks_new_blobs_against_tagged_remote_tip(repo, monkeypatch, size, expected):
    module = _load_module()
    monkeypatch.setattr(module, "REPO", repo)
    _write_bytes(repo, "src/existing.txt", 20)
    _commit_all(repo)
    _git(repo, "tag", "-a", "published", "-m", "published baseline")
    published = module._rev_parse("refs/tags/published")
    _write_bytes(repo, "src/introduced.json", size)
    _commit_all(repo)
    _git(repo, "tag", "-a", "candidate", "-m", "candidate")
    candidate = module._rev_parse("refs/tags/candidate")
    assert published and candidate
    result = _run(repo, "--base", published, "--head", candidate, "--published", published)
    assert result.returncode == expected, result.stdout + result.stderr
    assert "malformed" not in result.stdout
    if expected:
        assert "introduced.json" in result.stdout


def test_a_small_file_passes(repo: Path):
    _write_bytes(repo, "src/small.txt", 1024)
    _commit_all(repo)

    result = _run(repo, "--all")

    assert result.returncode == 0, result.stdout + result.stderr


def test_an_oversized_non_image_file_fails(repo: Path):
    _write_bytes(repo, "src/big.json", 2 * 1024 * 1024)
    _commit_all(repo)

    result = _run(repo, "--all")

    assert result.returncode == 1
    assert "big.json" in result.stdout
    assert "non-image" in result.stdout


def test_an_image_under_the_image_cap_passes(repo: Path):
    _write_bytes(repo, "docs/assets/screenshot.png", 2 * 1024 * 1024)
    _commit_all(repo)

    result = _run(repo, "--all")

    assert result.returncode == 0, result.stdout + result.stderr


def test_an_image_over_the_image_cap_fails(repo: Path):
    _write_bytes(repo, "docs/assets/screenshot.png", 4 * 1024 * 1024)
    _commit_all(repo)

    result = _run(repo, "--all")

    assert result.returncode == 1
    assert "screenshot.png" in result.stdout
    assert "image" in result.stdout


@pytest.mark.parametrize("ext", [".map", ".diff", ".patch"])
def test_always_blocked_extensions_fail_regardless_of_size(repo: Path, ext: str):
    _write_bytes(repo, f"dist/bundle{ext}", 10)
    _commit_all(repo)

    result = _run(repo, "--all")

    assert result.returncode == 1
    assert f"bundle{ext}" in result.stdout


def test_diff_scoped_mode_ignores_pre_existing_oversized_files(repo: Path):
    # A file already over cap on the base commit must never block an
    # unrelated later change that doesn't touch it -- the same attribution
    # fairness check-module-size.py's --changed-since guarantees.
    _write_bytes(repo, "src/already-big.json", 2 * 1024 * 1024)
    _commit_all(repo)
    _git(repo, "branch", "-f", "base_marker", "HEAD")

    _write_bytes(repo, "src/small.txt", 10)
    _commit_all(repo)

    result = _run(repo, "--base", "base_marker")

    assert result.returncode == 0, result.stdout + result.stderr


def test_diff_scoped_mode_catches_a_newly_added_oversized_file(repo: Path):
    _write_bytes(repo, "src/small.txt", 10)
    _commit_all(repo)
    _git(repo, "branch", "-f", "base_marker", "HEAD")

    _write_bytes(repo, "src/new-big.json", 2 * 1024 * 1024)
    _commit_all(repo)

    result = _run(repo, "--base", "base_marker")

    assert result.returncode == 1
    assert "new-big.json" in result.stdout


def test_object_scope_handles_unrelated_published_histories(repo: Path):
    """Object reachability works without a commit merge-base."""
    _write_bytes(repo, "src/small.txt", 10)
    _commit_all(repo)
    original_branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    _git(repo, "checkout", "-q", "--orphan", "rewritten-main")
    _write_bytes(repo, "unrelated.txt", 10)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "unrelated root (simulates a rewritten main)")
    _write_bytes(repo, "huge-in-rewritten-main.json", 2 * 1024 * 1024)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "oversized file only reachable from the rewritten line")
    _git(repo, "branch", "-f", "base_marker", "HEAD")
    _git(repo, "checkout", "-q", original_branch)

    result = _run(repo, "--base", "base_marker")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "huge-in-rewritten-main.json" not in result.stdout
    assert "skipping" not in result.stdout.lower()


def test_explicit_staged_paths_mode(repo: Path):
    _write_bytes(repo, "src/big.json", 2 * 1024 * 1024)
    _git(repo, "add", "src/big.json")

    result = _run(repo, "src/big.json")

    assert result.returncode == 1
    assert "big.json" in result.stdout


def test_deleted_file_in_diff_is_skipped(repo: Path):
    _write_bytes(repo, "src/to-delete.json", 2 * 1024 * 1024)
    _commit_all(repo)
    _git(repo, "branch", "-f", "base_marker", "HEAD")

    (repo / "src" / "to-delete.json").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "remove big file")

    result = _run(repo, "--base", "base_marker")

    assert result.returncode == 0, result.stdout + result.stderr


def test_renaming_an_existing_blob_does_not_reintroduce_it(repo: Path):
    _write_bytes(repo, "docs/assets/picture.png", 2 * 1024 * 1024)
    _commit_all(repo)
    _git(repo, "branch", "-f", "base_marker", "HEAD")

    _git(repo, "mv", "docs/assets/picture.png", "docs/assets/picture.json")
    _git(repo, "commit", "-q", "-m", "rename to dodge the image cap")

    result = _run(repo, "--base", "base_marker")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 newly-introduced blob(s)" in result.stdout


def test_existing_blob_renamed_to_blocked_extension_is_not_a_blob_introduction(repo: Path):
    _write_bytes(repo, "notes/small.txt", 10)
    _commit_all(repo)
    _git(repo, "branch", "-f", "base_marker", "HEAD")

    _git(repo, "mv", "notes/small.txt", "notes/small.patch")
    _git(repo, "commit", "-q", "-m", "rename to dodge the denylist")

    result = _run(repo, "--base", "base_marker")

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_filename_with_special_characters_is_still_checked(repo: Path):
    # Plain (non -z) git diff/ls-files output quotes non-ASCII/whitespace
    # filenames -- the guard must use NUL-delimited output throughout so an
    # oversized file under such a name is never silently treated as missing.
    _write_bytes(repo, "docs/café notes.json", 2 * 1024 * 1024)
    _commit_all(repo)

    result = _run(repo, "--all")

    assert result.returncode == 1
    assert "café notes.json" in result.stdout


def test_staged_mode_checks_the_index_not_the_working_tree(repo: Path):
    # Stage an oversized file, then shrink its working-tree copy WITHOUT
    # re-staging -- the index still holds the oversized blob that would
    # actually be committed, so the check must still fail.
    _write_bytes(repo, "src/staged-big.json", 2 * 1024 * 1024)
    _git(repo, "add", "src/staged-big.json")
    _write_bytes(repo, "src/staged-big.json", 10)  # shrink working tree only

    result = _run(repo, "src/staged-big.json")

    assert result.returncode == 1
    assert "staged-big.json" in result.stdout


def test_staged_mode_does_not_false_positive_on_working_tree_growth(repo: Path):
    # The reverse of the above: a small staged blob whose working-tree copy
    # has since grown past the cap (not yet re-staged) must still pass --
    # the index, not an unstaged edit, is what would actually be committed.
    _write_bytes(repo, "src/staged-small.json", 10)
    _git(repo, "add", "src/staged-small.json")
    _write_bytes(repo, "src/staged-small.json", 2 * 1024 * 1024)  # grow working tree only

    result = _run(repo, "src/staged-small.json")

    assert result.returncode == 0, result.stdout + result.stderr


def test_diff_scoped_mode_catches_an_oversized_file_added_then_deleted_in_range(repo: Path):
    # The oversized blob is still permanently in the pushed history the
    # instant the add commit lands, even though HEAD's own tree no longer
    # references it -- a naive tree-to-tree diff would miss this entirely.
    _write_bytes(repo, "src/small.txt", 10)
    _commit_all(repo)
    _git(repo, "branch", "-f", "base_marker", "HEAD")

    _write_bytes(repo, "src/transient-big.json", 2 * 1024 * 1024)
    _commit_all(repo)
    (repo / "src" / "transient-big.json").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "remove it again, same push")

    result = _run(repo, "--base", "base_marker")

    assert result.returncode == 1
    assert "transient-big.json" in result.stdout


@pytest.mark.parametrize("new_content", [True, False])
def test_transient_blocked_path_checks_only_new_blob_objects(repo: Path, new_content):
    _write_bytes(repo, "src/small.txt", 10)
    _commit_all(repo)
    _git(repo, "branch", "-f", "base_marker", "HEAD")

    _write_bytes(repo, "dist/transient.patch", 17 if new_content else 10)
    _commit_all(repo)
    (repo / "dist" / "transient.patch").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "remove it again, same push")

    result = _run(repo, "--base", "base_marker")

    assert (result.returncode == 1) is new_content, result.stdout + result.stderr
    if new_content:
        assert "transient.patch" in result.stdout
    else:
        assert "0 newly-introduced blob(s)" in result.stdout


def test_diff_scoped_mode_catches_a_file_oversized_in_an_earlier_commit_then_shrunk(repo: Path):
    # Same shape as the add-then-delete case, but shrunk back under cap
    # rather than deleted outright -- the earlier, oversized blob is still
    # what got pushed and must still be reported.
    _write_bytes(repo, "src/small.txt", 10)
    _commit_all(repo)
    _git(repo, "branch", "-f", "base_marker", "HEAD")

    _write_bytes(repo, "src/shrinks.json", 2 * 1024 * 1024)
    _commit_all(repo)
    _write_bytes(repo, "src/shrinks.json", 10)
    _commit_all(repo)

    result = _run(repo, "--base", "base_marker")

    assert result.returncode == 1
    assert "shrinks.json" in result.stdout


def test_all_mode_respects_an_explicit_head_other_than_the_checkout(repo: Path):
    _write_bytes(repo, "src/small.txt", 10)
    _commit_all(repo)
    _git(repo, "branch", "other")
    _git(repo, "checkout", "-q", "other")
    _write_bytes(repo, "src/big-on-other.json", 2 * 1024 * 1024)
    _commit_all(repo)
    _git(repo, "checkout", "-q", "-")  # back to the original branch; "other" not checked out

    result = _run(repo, "--all", "--head", "other")

    assert result.returncode == 1
    assert "big-on-other.json" in result.stdout

    # The currently-checked-out branch itself is unaffected.
    result_default = _run(repo, "--all")
    assert result_default.returncode == 0, result_default.stdout + result_default.stderr


def test_explicit_staged_paths_requires_double_dash_for_flag_shaped_names(repo: Path):
    # A staged file literally named "--all" must be checked as a path, not
    # parsed as the --all flag (which would silently switch to full-tree
    # mode against HEAD, never inspecting the actual staged content).
    _write_bytes(repo, "src/small.txt", 10)
    _commit_all(repo)
    _write_bytes(repo, "--all", 2 * 1024 * 1024)
    _git(repo, "add", "--", "--all")

    result = _run(repo, "--", "--all")

    assert result.returncode == 1
    assert "--all" in result.stdout


def test_diff_scoped_mode_catches_a_filename_with_special_characters(repo: Path):
    # Same rationale as the --all-mode special-character test above, but
    # for diff mode's own per-commit enumeration (git diff-tree), which has
    # an independent code path and quoting behavior from --all's ls-tree.
    _write_bytes(repo, "src/small.txt", 10)
    _commit_all(repo)
    _git(repo, "branch", "-f", "base_marker", "HEAD")

    _write_bytes(repo, "docs/café notes.json", 2 * 1024 * 1024)
    _commit_all(repo)

    result = _run(repo, "--base", "base_marker")

    assert result.returncode == 1
    assert "café notes.json" in result.stdout


def test_all_mode_fails_loudly_on_an_unresolvable_head(repo: Path):
    # An ls-tree failure (e.g. a bad --head) must be a hard failure, never
    # a silent [OK] from an empty-looking inventory.
    _write_bytes(repo, "src/small.txt", 10)
    _commit_all(repo)

    result = _run(repo, "--all", "--head", "no-such-ref")

    assert result.returncode == 1


def test_staged_mode_handles_a_digit_colon_filename_correctly(repo: Path):
    # A literal path beginning with "<digit>:" (e.g. "0:large.json") must
    # be resolved as that exact path, not misparsed as git's own explicit
    # ":<stage>:<path>" revision syntax (which would silently check a
    # DIFFERENT path -- stage 0 of "large.json" -- instead).
    _stage_blob(repo, "0:large.json", 2 * 1024 * 1024)

    result = _run(repo, "--", "0:large.json")

    assert result.returncode == 1
    assert "0:large.json" in result.stdout


def test_staged_mode_handles_a_magic_pathspec_shaped_filename(repo: Path):
    # A literal filename that happens to look like a git pathspec "magic"
    # signature (e.g. ":(literal)...") must still be resolved as that exact
    # path, not reinterpreted as the magic signature itself.
    name = ":(literal)large.json"
    _stage_blob(repo, name, 2 * 1024 * 1024)

    result = _run(repo, "--", name)

    assert result.returncode == 1
    assert name in result.stdout


def test_diff_scoped_mode_excludes_content_inherited_from_a_merges_other_parent(repo: Path):
    # A feature branch merging its own (advanced) base branch in brings
    # along content the base branch already introduced -- that content
    # isn't new to the DIFF being checked and must not be blamed on the
    # merge commit, even though a plain diff-vs-first-parent would
    # otherwise see it as "touched" by the merge.
    _write_bytes(repo, "src/small.txt", 10)
    _commit_all(repo)  # common ancestor
    _git(repo, "branch", "devbranch")
    _git(repo, "branch", "feature")

    _git(repo, "checkout", "-q", "devbranch")
    _write_bytes(repo, "big-on-dev.json", 2 * 1024 * 1024)
    _commit_all(repo)  # dev's own advance, not this PR's content

    _git(repo, "checkout", "-q", "feature")
    _write_bytes(repo, "feature-file.txt", 10)
    _commit_all(repo)
    _git(repo, "merge", "--no-edit", "-q", "devbranch")  # brings big-on-dev.json in

    result = _run(repo, "--base", "devbranch")

    assert result.returncode == 0, result.stdout + result.stderr


def test_diff_scoped_mode_skips_submodule_gitlinks_without_error(repo: Path, tmp_path: Path):
    # A submodule's gitlink entry (mode 160000) names a COMMIT in another
    # repository, not a blob in this one -- cat-file -s on it fails, which
    # must be treated as a deliberate, silent skip, never conflated with a
    # genuine blob-read failure.
    _write_bytes(repo, "src/small.txt", 10)
    _commit_all(repo)
    _git(repo, "branch", "-f", "base_marker", "HEAD")

    sub = tmp_path.parent / f"{tmp_path.name}-sub"
    sub.mkdir()
    _git(sub, "init", "-q")
    _git(sub, "config", "user.email", "test@example.com")
    _git(sub, "config", "user.name", "Test")
    (sub / "f.txt").write_text("hi")
    _git(sub, "add", "-A")
    _git(sub, "commit", "-q", "-m", "sub commit")

    _git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "vendor/sub")
    _git(repo, "commit", "-q", "-m", "add submodule")

    result = _run(repo, "--base", "base_marker")

    assert result.returncode == 0, result.stdout + result.stderr


def test_staged_mode_skips_submodule_gitlinks_without_error(repo: Path, tmp_path: Path):
    # Staged-mode sibling of the diff-mode test above: adding/updating a
    # submodule stages a mode-160000 gitlink entry too, and must be
    # skipped the same way (not every call site shares one code path here).
    sub = tmp_path.parent / f"{tmp_path.name}-sub2"
    sub.mkdir()
    _git(sub, "init", "-q")
    _git(sub, "config", "user.email", "test@example.com")
    _git(sub, "config", "user.name", "Test")
    (sub / "f.txt").write_text("hi")
    _git(sub, "add", "-A")
    _git(sub, "commit", "-q", "-m", "sub commit")

    _git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "vendor/sub")

    result = _run(repo, "--", "vendor/sub")

    assert result.returncode == 0, result.stdout + result.stderr


def test_new_blob_metadata_failure_raises_instead_of_skipping(repo: Path):
    _commit_all(repo)
    _git(repo, "branch", "base_marker")
    _write_bytes(repo, "src/big.json", 2 * 1024 * 1024)
    _commit_all(repo)

    module = _load_module()
    real_run = subprocess.run

    def _failing_cat_file(args, **kwargs):
        if "cat-file" in args:
            return subprocess.CompletedProcess(args, 1, b"", b"fatal: simulated failure")
        return real_run(args, **kwargs)

    with mock.patch.object(module, "REPO", repo), mock.patch("subprocess.run", side_effect=_failing_cat_file):
        with pytest.raises(module.GitEnumerationError):
            module._new_blobs_in_range("base_marker", "HEAD")


def test_staged_mode_handles_a_dot_slash_prefixed_path(repo: Path):
    # A caller-supplied "./src/big.json" must still be checked even though
    # `git ls-files` itself normalizes and reports it as "src/big.json" --
    # looking the entry back up under the ORIGINAL (unnormalized) spelling
    # would silently find nothing staged under that exact string.
    _write_bytes(repo, "src/big.json", 2 * 1024 * 1024)
    _git(repo, "add", "src/big.json")

    result = _run(repo, "./src/big.json")

    assert result.returncode == 1
    assert "big.json" in result.stdout


def test_an_inherited_git_dir_does_not_redirect_the_check_elsewhere(repo: Path, tmp_path: Path):
    # An ambient GIT_DIR/GIT_WORK_TREE (e.g. inherited from a parent
    # process already operating on a DIFFERENT repository) must never
    # override this script's own explicit `git -C REPO` -- otherwise
    # --all could silently inspect the wrong repository and report a
    # clean pass despite this one having a real violation.
    _write_bytes(repo, "src/big.json", 2 * 1024 * 1024)
    _commit_all(repo)

    other = tmp_path.parent / f"{tmp_path.name}-other"
    other.mkdir()
    _git(other, "init", "-q")
    _git(other, "config", "user.email", "test@example.com")
    _git(other, "config", "user.name", "Test")
    (other / "small.txt").write_text("hi")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "unrelated repo")

    env = {**os.environ, "GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other)}
    result = subprocess.run(
        [sys.executable, str(repo / "tools" / SCRIPT.name), "--all"],
        cwd=repo, capture_output=True, text=True, env=env,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "big.json" in result.stdout


def test_many_paths_to_one_new_blob_are_checked_once_per_mode(repo: Path):
    _commit_all(repo)
    _git(repo, "branch", "base_marker")
    for index in range(32):
        _write_bytes(repo, f"src/file-{index}.txt", 17)
    _git(repo, "add", "src")
    module = _load_module()
    calls = []
    original = module._git_bytes

    def observed(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    with mock.patch.object(module, "REPO", repo), mock.patch.object(module, "_git_bytes", observed):
        staged = module._staged_blobs([f"src/file-{index}.txt" for index in range(32)])
        _git(repo, "commit", "-qm", "introduce one blob under many paths")
        rows = module._new_blobs_in_range("base_marker", "HEAD")
    assert sum(args == ("cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)") for args in calls) == 2
    assert sum(args == ("cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize) %(rest)") for args in calls) == 1
    assert len(calls) <= 8
    assert rows == [("src/file-0.txt", 17)]
    assert staged == [("src/file-0.txt", 17)]


def test_blob_metadata_batch_deduplicates_objects_not_paths():
    module = _load_module()
    first, second = "a" * 40, "b" * 40
    response = f"{first} blob 0\n{second} blob 17\n".encode("ascii")
    with mock.patch.object(module, "_git_bytes", return_value=subprocess.CompletedProcess(
        ["git"], 0, response, b"",
    )) as invoked:
        assert module._blob_sizes([first, second, first]) == {first: 0, second: 17}
    invoked.assert_called_once_with(
        "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        input=f"{first}\n{second}\n".encode("ascii"),
    )


@pytest.mark.parametrize("response", [
    b"", b"a" * 40 + b" missing\n", b"a" * 40 + b" tree 17\n",
    b"b" * 40 + b" blob 17\n", b"a" * 40 + b" blob -1\n",
    b"a" * 40 + b" blob invalid\n", b"a" * 40 + b" blob 17 extra\n",
    (b"a" * 40 + b" blob 17\n") * 2,
])
def test_missing_or_malformed_blob_batch_is_not_a_success(response):
    module = _load_module()
    with mock.patch.object(module, "_git_bytes", return_value=subprocess.CompletedProcess(
        ["git"], 0, response, b"",
    )):
        with pytest.raises(module.GitEnumerationError):
            module._blob_sizes(["a" * 40])


def test_empty_or_invalid_object_batch_never_spawns_git():
    module = _load_module()
    with mock.patch.object(module, "_git_bytes", side_effect=AssertionError):
        assert module._blob_sizes([]) == {}
        with pytest.raises(module.GitEnumerationError):
            module._blob_sizes(["a" * 40 + "\nHEAD"])


def test_staged_existing_blob_is_not_a_new_introduction(repo: Path):
    _write_bytes(repo, "existing.json", 2 * 1024 * 1024)
    _commit_all(repo)
    (repo / "copied.json").write_bytes((repo / "existing.json").read_bytes())
    _git(repo, "add", "copied.json")
    result = _run(repo, "--", "existing.json", "copied.json")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 new staged blob(s)" in result.stdout


def test_unpublished_local_branch_does_not_exempt_a_new_blob(repo: Path):
    _commit_all(repo)
    _git(repo, "branch", "base_marker")
    _write_bytes(repo, "large.json", 2 * 1024 * 1024)
    _commit_all(repo)
    _git(repo, "branch", "unpublished")
    _git(repo, "checkout", "-qb", "candidate", "base_marker")
    _write_bytes(repo, "large.json", 2 * 1024 * 1024)
    _commit_all(repo)
    result = _run(repo, "--base", "base_marker")
    assert result.returncode == 1 and "large.json" in result.stdout


def test_rebased_candidate_does_not_reintroduce_published_blobs(repo: Path):
    _commit_all(repo)
    _git(repo, "branch", "base_marker")
    _write_bytes(repo, "published-large.json", 2 * 1024 * 1024)
    _commit_all(repo)
    _git(repo, "branch", "published-tip")
    _git(repo, "checkout", "-qb", "advanced", "base_marker")
    (repo / "advance.txt").write_text("already published advance")
    _commit_all(repo)
    _git(repo, "branch", "-f", "base_marker", "HEAD")
    _write_bytes(repo, "published-large.json", 2 * 1024 * 1024)
    (repo / "new.txt").write_text("genuinely new blob")
    _commit_all(repo)
    result = _run(repo, "--base", "base_marker", "--published", "published-tip")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 newly-introduced blob(s)" in result.stdout


def test_new_blob_at_multiple_paths_still_checks_a_disallowed_name(repo: Path):
    _commit_all(repo)
    _git(repo, "branch", "base_marker")
    _write_bytes(repo, "new.txt", 17)
    _write_bytes(repo, "new.patch", 17)
    _commit_all(repo)
    result = _run(repo, "--base", "base_marker")
    assert result.returncode == 1 and "new.patch" in result.stdout


def test_unavailable_explicit_published_tip_fails_closed(repo: Path):
    _commit_all(repo)
    result = _run(repo, "--base", "HEAD", "--published", "unavailable-tip")
    assert result.returncode == 1 and "published baseline is unavailable" in result.stdout


def test_first_publication_with_zero_old_tip_checks_all_introduced_blobs(repo: Path):
    _write_bytes(repo, "new-large.json", 2 * 1024 * 1024)
    _commit_all(repo)
    result = _run(repo, "--base", "0" * 40)
    assert result.returncode == 1 and "new-large.json" in result.stdout


@pytest.mark.parametrize("arguments,message", [
    (("--base", "unavailable-base"), "publication base is unavailable"),
    (("--base", "HEAD", "--head", "unavailable-head"), "publication head is unavailable"),
])
def test_missing_publication_history_never_means_no_new_blobs(repo: Path, arguments, message):
    _commit_all(repo)
    result = _run(repo, *arguments)
    assert result.returncode == 1 and message in result.stdout
