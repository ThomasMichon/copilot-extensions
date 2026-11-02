"""Synthetic immutable-source controls; no Git, Docker or service process."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from test_reproducer_lab_monitoring import LAB

spec = importlib.util.spec_from_file_location("reproducer_lab_source", LAB / "source.py")
assert spec is not None and spec.loader is not None
source = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = source
spec.loader.exec_module(source)
COMMIT, TREE, BLOB = "a" * 40, "b" * 40, "c" * 40
LISTING = f"100644 blob {BLOB} 7\tmodule.py\0"


def test_only_regular_bounded_tree_metadata_is_accepted() -> None:
    observed = source.inspect_tree(COMMIT, TREE, LISTING)
    assert observed == source.SourceTree(COMMIT, TREE, 1, 7)
    assert not hasattr(observed, "ready")


@pytest.mark.parametrize("listing", [
    "", LISTING[:-1], LISTING + LISTING,
    f"120000 blob {BLOB} 7\tlinked\0", f"160000 commit {BLOB} -\tsubmodule\0",
    f"100644 blob {BLOB} 7\t/absolute\0", f"100644 blob {BLOB} 7\t../foreign\0",
    f"100644 blob {BLOB} 7\t.git/config\0", f"100644 blob {BLOB} 7\ta//b\0",
    f"100644 blob {BLOB} 7\ta/./b\0", f"100644 blob {BLOB} 7\t{'x' * 1025}\0",
    f"100644 blob {BLOB} -1\tmodule.py\0", f"100644 blob {BLOB} unknown\tmodule.py\0",
    "100644 blob short 7\tmodule.py\0", f"100644 blob {BLOB} 8388609\tmodule.py\0",
])
def test_unsafe_incomplete_or_unbounded_source_tree_refuses(listing: str) -> None:
    with pytest.raises(source.SourceError):
        source.inspect_tree(COMMIT, TREE, listing)


def test_source_tree_aggregate_input_ceilings(monkeypatch: Any) -> None:
    monkeypatch.setattr(source, "MAX_FILES", 1)
    with pytest.raises(source.SourceError, match="input ceiling"):
        source.inspect_tree(COMMIT, TREE, LISTING + LISTING.replace("module.py", "other.py"))
    monkeypatch.setattr(source, "MAX_SOURCE_BYTES", 6)
    with pytest.raises(source.SourceError, match="input ceiling"):
        source.inspect_tree(COMMIT, TREE, LISTING)


@pytest.mark.parametrize("names", [
    ("A.py", "a.py"),
    ("Package/a.py", "package/b.py"),
    ("module", "MODULE/child.py"),
    ("module/child.py", "MODULE"),
    ("\u00e9.py", "e\u0301.py"),
    ("\u03b1\u0345\u0301.py", "\u03ac\u0345.py"),
    ("Stra\u00dfe/a.py", "STRASSE/b.py"),
    (".GIT/config",),
    ("package/.GiT/config",),
])
def test_case_normalization_and_directory_aliases_refuse_before_checkout(
    tmp_path: Path, names: tuple[str, ...],
) -> None:
    listing = "".join(f"100644 blob {BLOB} 7\t{name}\0" for name in names)
    repository, results = roots(tmp_path)
    runner = FakeGit(listing=listing)
    with pytest.raises(source.SourceError, match="path aliases"):
        source.SourceReader(Path("/selected/git"), run=runner).materialize(
            repository, COMMIT, results, "attempt",
        )
    assert len(runner.calls) == 3
    assert not (results / "attempt" / "source.json").exists()
    assert not (results / "attempt" / "source").exists()


def test_shared_exact_directory_prefixes_remain_supported() -> None:
    listing = "".join(
        f"100644 blob {BLOB} 7\t{name}\0"
        for name in ("Package/a.py", "Package/b.py", "other/Package/a.py")
    )
    assert source.inspect_tree(COMMIT, TREE, listing).files == 3


def test_prefix_entry_and_utf8_byte_ceilings_are_enforced_before_insertion(monkeypatch: Any) -> None:
    prefixes: dict[str, tuple[str, bool]] = {}
    monkeypatch.setattr(source, "MAX_PREFIXES", 2)
    monkeypatch.setattr(source, "MAX_PREFIX_BYTES", 28)
    used = source._check_path_aliases("shared/a", prefixes, 0)
    assert used == 28 and len(prefixes) == 2
    with pytest.raises(source.SourceError, match="prefix inventory"):
        source._check_path_aliases("shared/b", prefixes, used)
    assert len(prefixes) == 2
    monkeypatch.setattr(source, "MAX_PREFIXES", 3)
    monkeypatch.setattr(source, "MAX_PREFIX_BYTES", 43)
    with pytest.raises(source.SourceError, match="prefix inventory"):
        source._check_path_aliases("shared/b", prefixes, used)
    assert len(prefixes) == 2
    monkeypatch.setattr(source, "MAX_PREFIX_BYTES", 44)
    assert source._check_path_aliases("shared/b", prefixes, used) == 44
    assert len(prefixes) == 3


def test_deep_zero_byte_paths_cannot_expand_preflight_memory_without_bound() -> None:
    listing = "".join(
        f"100644 blob {BLOB} 0\tfile-{index}/{'a/' * 480}end\0" for index in range(128)
    )
    assert len(listing.encode("utf-8")) < source.MAX_LISTING_BYTES
    with pytest.raises(source.SourceError, match="prefix inventory"):
        source.inspect_tree(COMMIT, TREE, listing)


@pytest.mark.parametrize("limit", [len(LISTING.encode("utf-8")) - 1, 1])
def test_direct_tree_inspection_also_bounds_listing_bytes(monkeypatch: Any, limit: int) -> None:
    monkeypatch.setattr(source, "MAX_LISTING_BYTES", limit)
    with pytest.raises(source.SourceError, match="listing exceeds"):
        source.inspect_tree(COMMIT, TREE, LISTING)


def test_listing_limit_counts_utf8_bytes_and_accepts_the_exact_ceiling(monkeypatch: Any) -> None:
    listing = LISTING.replace("module.py", "\u00e9.py")
    monkeypatch.setattr(source, "MAX_LISTING_BYTES", len(listing))
    with pytest.raises(source.SourceError, match="listing exceeds"):
        source.inspect_tree(COMMIT, TREE, listing)
    monkeypatch.setattr(source, "MAX_LISTING_BYTES", len(listing.encode("utf-8")))
    assert source.inspect_tree(COMMIT, TREE, listing).files == 1


def test_exact_tree_and_blob_input_ceilings(monkeypatch: Any) -> None:
    maximum = f"100644 blob {BLOB} {source.MAX_BLOB_BYTES}\tmodule.py\0"
    assert source.inspect_tree(COMMIT, TREE, maximum).logical_bytes == source.MAX_BLOB_BYTES
    monkeypatch.setattr(source, "MAX_FILES", 2)
    monkeypatch.setattr(source, "MAX_SOURCE_BYTES", 14)
    two = LISTING + LISTING.replace("module.py", "second.py")
    assert source.inspect_tree(COMMIT, TREE, two).files == 2
    with pytest.raises(source.SourceError):
        source.inspect_tree(COMMIT, TREE, two + LISTING.replace("module.py", "third.py"))


def test_git_sha256_source_tree_and_mixed_identity_refusal() -> None:
    commit, tree, blob = ("a" * 64, "b" * 64, "c" * 64)
    assert source.inspect_tree(commit, tree, f"100644 blob {blob} 7\tmodule.py\0").files == 1
    with pytest.raises(source.SourceError):
        source.inspect_tree(commit, TREE, LISTING)
    with pytest.raises(source.SourceError):
        source.inspect_tree(commit, tree, LISTING)


class FakeGit:
    def __init__(self, commit: str = COMMIT, tree: str = TREE, listing: str = LISTING) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.fail_at = 0
        self.commit, self.tree, self.listing = commit, tree, listing

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        if len(self.calls) == self.fail_at:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        operation = argv[argv.index("-C") + 2]
        output = ""
        if operation == "rev-parse":
            output = self.tree if "tree" in argv[-1] else self.commit
        elif operation == "ls-tree":
            output = self.listing
        return subprocess.CompletedProcess(argv, 0, output, "")


def roots(tmp_path: Path) -> tuple[Path, Path]:
    repository, results = tmp_path / "repo", tmp_path / "results"
    repository.mkdir()
    results.mkdir(mode=0o700)
    return repository, results


@pytest.mark.parametrize("commit,tree,listing,object_format", [
    (COMMIT, TREE, LISTING, "sha1"),
    ("a" * 64, "b" * 64, f"100644 blob {'c' * 64} 7\tmodule.py\0", "sha256"),
])
def test_materializer_uses_original_commit_clean_environment_and_bounded_owner(
    tmp_path: Path, monkeypatch: Any, commit: str, tree: str, listing: str, object_format: str,
) -> None:
    repository, results = roots(tmp_path)
    monkeypatch.setenv("SYNTHETIC_PRIVATE_INPUT", "must-not-inherit")
    monkeypatch.setenv("GIT_DIR", "/foreign")
    runner = FakeGit(commit, tree, listing)
    client = source.SourceReader(Path("/selected/git"), run=runner, clock=lambda: 0)
    observed = client.materialize(repository, commit, results, "attempt")
    assert observed.commit == commit
    assert len(runner.calls) == 8
    for argv, kwargs in runner.calls:
        assert argv[0] == "/selected/git"
        assert 0 < kwargs["timeout"] <= 15
        assert kwargs["max_stdout_bytes"] == 4 * 1024**2
        env = kwargs["env"]
        assert "SYNTHETIC_PRIVATE_INPUT" not in env and "GIT_DIR" not in env
        assert env["GIT_ALLOW_PROTOCOL"] == "file"
        assert env["GIT_NO_REPLACE_OBJECTS"] == "1"
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_ATTR_NOSYSTEM"] == "1"
        assert "core.attributesFile=" + source.os.devnull in argv
    fetch = next(argv for argv, _ in runner.calls if "fetch" in argv)
    assert fetch[-2:] == [str(repository), commit]
    assert {"--depth=1", "--no-tags", "--no-write-fetch-head", "--no-auto-maintenance"} <= set(fetch)
    init = next(argv for argv, _ in runner.calls if "init" in argv)
    assert f"--object-format={object_format}" in init
    assert json.loads((results / "attempt" / "source.json").read_text())["tree"] == tree
    with pytest.raises(FileExistsError):
        client.materialize(repository, commit, results, "attempt")
    assert len(runner.calls) == 8


@pytest.mark.parametrize("fail_at", range(1, 9))
def test_failed_source_attempt_is_retained_and_not_retried(tmp_path: Path, fail_at: int) -> None:
    repository, results = roots(tmp_path)
    runner = FakeGit()
    runner.fail_at = fail_at
    client = source.SourceReader(Path("/selected/git"), run=runner, clock=lambda: 0)
    with pytest.raises(source.SourceError, match="TimeoutExpired"):
        client.materialize(repository, COMMIT, results, "attempt")
    assert len(runner.calls) == fail_at
    assert (results / "attempt").is_dir()
    assert not (results / "attempt" / "source.json").exists()


@pytest.mark.parametrize("name", ["../foreign", "/foreign", "a/b", "A", "x" * 65])
def test_artifact_name_cannot_escape_private_root(tmp_path: Path, name: str) -> None:
    repository, results = roots(tmp_path)
    with pytest.raises(source.SourceError):
        source.SourceReader(Path("/selected/git"), run=FakeGit()).materialize(
            repository, COMMIT, results, name,
        )
    assert list(results.iterdir()) == []


def test_public_or_linked_results_root_is_not_accepted(tmp_path: Path) -> None:
    repository, results = roots(tmp_path)
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    link = tmp_path / "linked"
    link.symlink_to(results, target_is_directory=True)
    for root in (public, link, repository):
        with pytest.raises(source.SourceError):
            source.SourceReader(Path("/selected/git"), run=FakeGit()).materialize(
                repository, COMMIT, root, "attempt",
            )


def test_final_source_command_settlement_is_inside_deadline(tmp_path: Path) -> None:
    repository, results = roots(tmp_path)
    runner = FakeGit()
    now = 0.0

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal now
        result = runner(argv, **kwargs)
        if len(runner.calls) == 8:
            now = 60.0
        return result

    client = source.SourceReader(Path("/selected/git"), run=run, clock=lambda: now)
    with pytest.raises(source.SourceError, match="deadline"):
        client.materialize(repository, COMMIT, results, "attempt")
    assert not (results / "attempt" / "source.json").exists()


@pytest.mark.parametrize("change_at", [1, 2, 7, 8])
def test_selected_and_materialized_identities_must_match(tmp_path: Path, change_at: int) -> None:
    repository, results = roots(tmp_path)
    runner = FakeGit()

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        result = runner(argv, **kwargs)
        if len(runner.calls) == change_at:
            result.stdout = "foreign"
        return result

    with pytest.raises(source.SourceError):
        source.SourceReader(Path("/selected/git"), run=run).materialize(
            repository, COMMIT, results, "attempt",
        )
    assert not (results / "attempt" / "source.json").exists()


def test_source_command_failure_does_not_echo_diagnostic_payload(tmp_path: Path) -> None:
    repository, results = roots(tmp_path)

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 42, "", "synthetic-sensitive-diagnostic")

    with pytest.raises(source.SourceError, match="status 42") as error:
        source.SourceReader(Path("/selected/git"), run=run).materialize(
            repository, COMMIT, results, "attempt",
        )
    assert "synthetic-sensitive" not in str(error.value)
