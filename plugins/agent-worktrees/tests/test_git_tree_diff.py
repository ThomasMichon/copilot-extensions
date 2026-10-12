"""Batched classifier comparisons: in-process policy and opt-in real Git."""

from __future__ import annotations

import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_worktrees import git_ops, git_tree_diff

pytestmark = [
    pytest.mark.contract("agent_worktrees.classify_tree_objects"),
    pytest.mark.guard,
]

_BASE = "1" * 40
_BRANCH = "2" * 40
_UPSTREAM = "3" * 40
_ZERO = "0" * 40


def _raw(path, before=_BASE, after=_BRANCH, *, status="M", modes="100644 100644"):
    return f":{modes} {before} {after} {status}\0{path}\0"


def _tree(path, object_id=_BRANCH, *, kind="blob", mode="100644"):
    return f"{mode} {kind} {object_id}\t{path}\0"


def _result(stdout="", *, code=0):
    return subprocess.CompletedProcess(["git"], code, stdout, "unavailable" if code else "")


@pytest.mark.portfolio_tier("T0")
@pytest.mark.parametrize("width", [40, 64])
def test_parser_preserves_literal_paths_and_full_object_ids(width):
    before, after = "1" * width, "2" * width
    paths = ["space name", "tab\tname", "line\nname", "quote\"name", "colon:name", "caf\u00e9"]
    output = "".join(_raw(path, before, after) for path in paths)
    assert git_tree_diff.parse(output) == {path: (before, after) for path in paths}
    assert git_tree_diff.parse("") == {}


@pytest.mark.portfolio_tier("T0")
@pytest.mark.parametrize("output", [
    " ",
    _raw("path")[:-1],
    ":100644 100644 short short M\0path\0",
    f":100644 100644 {_BASE} {'2' * 64} M\0path\0",
    f":100644 100644 {_BASE} {_BRANCH} R100\0old\0new\0",
    _raw(""),
    _raw("path") + _raw("path"),
    _raw("path") + "incomplete\0",
])
def test_parser_refuses_partial_or_ambiguous_evidence(output):
    with pytest.raises(git_tree_diff.TreeDiffError):
        git_tree_diff.parse(output)


def _model(monkeypatch, tmp_path, comparisons, *, fetch=False):
    (tmp_path / ".git").mkdir(exist_ok=True)
    calls = []
    diffs = iter(comparisons)

    def run(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("rev-parse", "--abbrev-ref"):
            return _result("worktree/example\n")
        assert kwargs["timeout"] == git_ops._CLASSIFY_GIT_TIMEOUT
        if args[0] in ("status", "fetch"):
            return _result()
        if args[0] == "rev-list":
            return _result("1\t1\n")
        if args[0] == "merge-base":
            return _result(_BASE + "\n")
        if args[:2] == ("--no-pager", "log"):
            return _result("Pending work\n")
        if args[0] == "cherry":
            return _result("+ " + _BRANCH + "\n")
        if args[0] in ("diff", "ls-tree"):
            result = next(diffs)
            if isinstance(result, Exception):
                raise result
            return result
        pytest.fail(f"unexpected Git child: {args}")

    monkeypatch.setattr(git_ops, "git", run)
    info = git_ops.classify_worktree(
        str(tmp_path), "tracked/old", default_branch="main", fetch=fetch,
    )
    return info, calls


@pytest.mark.portfolio_tier("T1")
@pytest.mark.effect("filesystem")
@pytest.mark.parametrize("branch,upstream,expected", [
    (_raw("file"), _tree("file", _UPSTREAM), "wip"),
    (_raw("file"), _tree("file"), "completed"),
    (_raw("mode", modes="100644 100755"), _tree("mode"), "completed"),
    (_raw("mode", modes="100644 100755"), _tree("mode", _UPSTREAM), "wip"),
    (_raw("added", _ZERO, _BRANCH, status="A"), _tree("added"), "completed"),
    (_raw("added", _ZERO, _BRANCH, status="A"), "", "wip"),
    (_raw("deleted", _BASE, _ZERO, status="D"), "", "completed"),
    (_raw("deleted", _BASE, _ZERO, status="D"), _tree("deleted", _BASE), "wip"),
    (_raw("deleted", _BASE, _ZERO, status="D"),
     _tree("deleted", _UPSTREAM, kind="tree", mode="040000"), "wip"),
    (_raw("old", _BASE, _ZERO, status="D") + _raw("new", _ZERO, _BASE, status="A"),
     _tree("old", _BASE) + _tree("new", _BASE), "wip"),
    (_raw("module", modes="160000 160000"),
     _tree("module", _UPSTREAM, kind="commit", mode="160000"), "wip"),
    (_raw("line\nname"), _tree("line\nname", _UPSTREAM), "wip"),
    ("", "", "unused"),
])
def test_classifier_batches_objects_without_masking_unmerged_paths(
    monkeypatch, tmp_path, branch, upstream, expected,
):
    info, calls = _model(monkeypatch, tmp_path, [_result(branch), _result(upstream)])
    assert info.state.value == expected
    assert (info.ahead, info.behind, info.dirty, info.title) == (1, 1, 0, "Pending work")
    assert info.current_branch == "worktree/example"
    assert info.branch_drift
    assert not info.fetch_requested and not info.fetch_failed
    assert len(calls) == (8 if branch else 7)
    diff_calls = [call for call in calls if call[0] == "diff"]
    assert diff_calls[0][-3:] == (_BASE, "worktree/example", "--")
    assert len(diff_calls) == 1
    if branch:
        lookup = [call for call in calls if call[0] == "ls-tree"]
        assert lookup[0][:5] == ("ls-tree", "--full-tree", "-z", "origin/main", "--")
        assert set(lookup[0][5:]) == {
            f":(literal){path}" for path in git_tree_diff.parse(branch)
        }
    for call in diff_calls:
        assert call[1:-3] == (
            "--raw", "--no-abbrev", "--no-renames", "--no-ext-diff",
            "--no-textconv", "--ignore-submodules=none", "--no-relative", "-z",
        )


@pytest.mark.portfolio_tier("T1")
@pytest.mark.effect("filesystem")
@pytest.mark.parametrize("stage", [0, 1])
@pytest.mark.parametrize("failure", [
    _result(_raw("partial"), code=128),
    _result("truncated"),
    subprocess.TimeoutExpired(["git", "diff"], git_ops._CLASSIFY_GIT_TIMEOUT),
])
def test_failed_tree_evidence_is_unknown_never_completed(
    monkeypatch, tmp_path, caplog, stage, failure,
):
    results = [_result(_raw("file")), _result()]
    results[stage] = failure
    info, _calls = _model(monkeypatch, tmp_path, results, fetch=True)
    assert info.state == git_ops.WorktreeState.UNKNOWN
    assert info.current_branch == "worktree/example" and info.branch_drift
    assert info.fetch_requested
    assert info.fetch_failed == isinstance(failure, subprocess.TimeoutExpired)
    assert caplog.records


@pytest.mark.portfolio_tier("T1")
@pytest.mark.effect("filesystem")
def test_many_paths_still_use_two_tree_reads_and_no_blob_children(monkeypatch, tmp_path):
    branch = "".join(_raw(str(index)) for index in range(1000))
    upstream = "".join(
        _tree(str(index), _UPSTREAM if index == 999 else _BRANCH) for index in range(1000)
    )
    info, calls = _model(monkeypatch, tmp_path, [
        _result(branch), _result(upstream),
    ])
    assert info.state == git_ops.WorktreeState.WIP
    assert len(calls) == 8
    assert [call for call in calls if call[0] == "rev-parse"] == [
        ("rev-parse", "--abbrev-ref", "HEAD"),
    ]


@pytest.mark.portfolio_tier("T0")
def test_comparisons_share_no_mutable_or_installation_state():
    def compare(installation):
        paths = git_tree_diff.read(
            lambda *args: _result(_raw(installation)),
            "base", "branch",
        )
        return set(paths)

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(compare, ["cell-a", "cell-b"])) == [{"cell-a"}, {"cell-b"}]
    assert compare("cell-c") == {"cell-c"}


@pytest.mark.portfolio_tier("T0")
@pytest.mark.parametrize("width", [40, 64])
def test_lookup_preserves_literal_names_and_all_tree_entry_types(width):
    object_id = "2" * width
    entries = [
        ("space name", "blob", "100644"),
        ("line\nname", "tree", "040000"),
        ("module", "commit", "160000"),
    ]
    output = "".join(
        _tree(path, object_id, kind=kind, mode=mode) for path, kind, mode in entries
    )
    objects = git_tree_diff.read_objects(
        lambda *args: _result(output), "upstream", [path for path, _kind, _mode in entries],
    )
    assert objects == {path: object_id for path, _kind, _mode in entries}


@pytest.mark.portfolio_tier("T0")
def test_lookup_bounds_arguments_and_keeps_ancestor_requests_separate(monkeypatch):
    monkeypatch.setattr(git_tree_diff, "_MAX_ARG_UNITS", 220)
    paths = ["dir", "dir/leaf", "colon:name", "star*[x]", "line\nname", "space name"]
    calls = []

    def run(*args):
        assert sum(git_tree_diff._argument_cost(arg) for arg in ("git", *args)) <= 220
        requested = [arg.removeprefix(":(literal)") for arg in args[5:]]
        assert not any(
            first != second and second.startswith(first + "/")
            for first in requested for second in requested
        )
        calls.append(requested)
        return _result("".join(_tree(path) for path in requested))

    assert git_tree_diff.read_objects(run, "upstream", paths) == dict.fromkeys(paths, _BRANCH)
    assert len(calls) > 1


@pytest.mark.portfolio_tier("T0")
@pytest.mark.parametrize("output", [
    " ",
    _tree("path")[:-1],
    "100644 blob short\tpath\0",
    _tree("path") + _tree("path"),
    _tree("not-requested"),
])
def test_lookup_rejects_incomplete_or_unrequested_entries(output):
    with pytest.raises(git_tree_diff.TreeDiffError):
        git_tree_diff.read_objects(lambda *args: _result(output), "upstream", ["path"])


@pytest.mark.portfolio_tier("T0")
def test_lookup_reports_oversized_path_without_spawning(monkeypatch):
    monkeypatch.setattr(git_tree_diff, "_MAX_ARG_UNITS", 120)
    with pytest.raises(git_tree_diff.TreeDiffError, match="argument budget"):
        git_tree_diff.read_objects(
            lambda *args: pytest.fail("oversized argv must not be spawned"),
            "upstream", ["x" * 200],
        )


def _native_fixture(root: Path, count: int) -> None:
    root.mkdir()
    git_ops.git("-c", "init.templateDir=", "init", "-q", "-b", "main", cwd=root)
    for name, value in [
        ("user.name", "Fixture"), ("user.email", "fixture@example.invalid"),
        ("core.hooksPath", str(root / "empty-hooks")),
    ]:
        git_ops.git("config", name, value, cwd=root)
    for index in range(count):
        (root / f"{index:03} caf\u00e9.txt").write_text("base\n", encoding="utf-8")
    git_ops.git("add", ".", cwd=root)
    git_ops.git("commit", "-q", "-m", "Base", cwd=root)
    git_ops.git("checkout", "-q", "-b", "worktree/example", cwd=root)
    for index in range(count):
        (root / f"{index:03} caf\u00e9.txt").write_text("shared\n", encoding="utf-8")
    git_ops.git("commit", "-q", "-am", "Pending work", cwd=root)
    git_ops.git("checkout", "-q", "main", cwd=root)
    for index in range(count):
        value = "different\n" if index == count - 1 else "shared\n"
        (root / f"{index:03} caf\u00e9.txt").write_text(value, encoding="utf-8")
    unrelated = root / "upstream-only"
    unrelated.mkdir()
    for index in range(500):
        (unrelated / f"{index:03}.txt").write_text("upstream only\n", encoding="utf-8")
    git_ops.git("add", ".", cwd=root)
    git_ops.git("commit", "-q", "-m", "Upstream work", cwd=root)
    git_ops.git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=root)
    git_ops.git("checkout", "-q", "worktree/example", cwd=root)


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(
    os.environ.get("AGENT_WORKTREES_TREE_DIFF_NATIVE") != "1",
    reason="opt-in real Git classifier budget and mutable-state integration",
)
@pytest.mark.parametrize("count", [1, 20])
def test_native_two_cycle_budget_and_fresh_mutable_state(monkeypatch, tmp_path, count):
    environment = git_ops.repository_identity_env()
    for name in set(os.environ) - set(environment):
        monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "checkout"
    _native_fixture(root, count)
    upstream = git_ops.git("rev-parse", "origin/main", cwd=root).stdout.strip()
    real_run = git_ops.subprocess.run
    starts = []

    def counted_run(cmd, **kwargs):
        result = real_run(cmd, **kwargs)
        if cmd[0] == "git":
            starts.append(tuple(cmd[1:]))
            if cmd[1] == "ls-tree":
                assert "upstream-only" not in result.stdout
                assert len(result.stdout.split("\0")[:-1]) <= count
        return result

    monkeypatch.setattr(git_ops.subprocess, "run", counted_run)

    def classify():
        starts.clear()
        begin = time.perf_counter()
        info = git_ops.classify_worktree(
            str(root), "worktree/example", default_branch="main",
        )
        elapsed = time.perf_counter() - begin
        return info, len(starts), elapsed

    for cycle in range(2):
        info, spawned, elapsed = classify()
        assert info.state == git_ops.WorktreeState.WIP
        assert (info.ahead, info.behind, info.dirty) == (1, 1, 0)
        assert spawned == 8
        print(f"files={count} cycle={cycle + 1} direct_git_starts={spawned} seconds={elapsed:.3f}")
    (root / "dirty.txt").write_text("new work\n", encoding="utf-8")
    assert classify()[0].state == git_ops.WorktreeState.DIRTY
    (root / "dirty.txt").unlink()
    git_ops.git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=root)
    assert classify()[0].state == git_ops.WorktreeState.COMPLETED
    git_ops.git("update-ref", "refs/remotes/origin/main", upstream, cwd=root)
    git_ops.git("config", "diff.renames", "true", cwd=root)
    assert classify()[0].state == git_ops.WorktreeState.WIP
    branch_head = git_ops.git("rev-parse", "HEAD", cwd=root).stdout.strip()
    assert git_tree_diff.read(
        lambda *args: git_ops.git(*args, cwd=root), "origin/main", "HEAD",
    )
    git_ops.git("replace", branch_head, upstream, cwd=root)
    assert git_tree_diff.read(
        lambda *args: git_ops.git(*args, cwd=root), "origin/main", "HEAD",
    ) == {}
    git_ops.git("replace", "-d", branch_head, cwd=root)
    monkeypatch.setattr(git_ops.subprocess, "run", real_run)
    exact = git_tree_diff.read_objects(
        lambda *args: git_ops.git(*args, cwd=root),
        "origin/main", ["upstream-only", "upstream-only/000.txt"],
    )
    assert set(exact) == {"upstream-only", "upstream-only/000.txt"}
    monkeypatch.setattr(git_ops.subprocess, "run", counted_run)
    git_ops.git("checkout", "-q", "main", cwd=root)
    (root / f"{count - 1:03} caf\u00e9.txt").write_text("shared\n", encoding="utf-8")
    git_ops.git("commit", "-q", "-am", "Land content with a different patch shape", cwd=root)
    git_ops.git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=root)
    git_ops.git("checkout", "-q", "worktree/example", cwd=root)
    info, spawned, _elapsed = classify()
    assert info.state == git_ops.WorktreeState.COMPLETED
    assert spawned == 8
