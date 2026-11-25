"""Exercise the trusted inline verification of a generated cleanup PR's paths."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

from _promotion_workflow_test_support import source_workflow

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "owner/project"
HEAD = "a" * 40


def run_script():
    steps = source_workflow(ROOT, "purge-consumed-changefiles.yml")["jobs"]["purge"]["steps"]
    return next(step["run"] for step in steps if "cleanup_branch=" in step.get("run", ""))


def verifier():
    bodies = re.findall(r"<<'VERIFY'\s*\n(.*?)^VERIFY\s*$", run_script(), re.M | re.S)
    assert len(bodies) == 1
    return bodies[0]


def removed(count, start=0):
    return [
        {"filename": f".changefiles/{index:05d}.json", "status": "removed"}
        for index in range(start, start + count)
    ]


def paged(files, size=100):
    return [files[index:index + size] for index in range(0, len(files), size)]


def pull(changed, head=HEAD, **change):
    pr = {
        "state": "open", "changed_files": changed, "head": {"sha": head},
        "base": {"ref": "dev", "repo": {"full_name": REPOSITORY}},
    }
    pr.update(change)
    return pr


def execute(monkeypatch, pulls, pages, number="7", error=None):
    calls = []
    pulls = list(pulls)

    def run(argv, **kwargs):
        calls.append(argv)
        if error:
            raise error
        path = argv[-1]
        if path == f"repos/{REPOSITORY}/pulls/{number}":
            return SimpleNamespace(stdout=json.dumps(pulls.pop(0)))
        assert argv[:4] == ["gh", "api", "--paginate", "--slurp"]
        assert path == f"repos/{REPOSITORY}/pulls/{number}/files?per_page=100"
        return SimpleNamespace(stdout=json.dumps(pages))

    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", ["-", number])
    exec(compile(verifier(), "<trusted-cleanup-verifier>", "exec"), {"__name__": "__main__"})
    return calls


def test_more_than_300_removed_changefiles_verify_across_all_pages(monkeypatch, capsys):
    files = removed(843)
    calls = execute(monkeypatch, [pull(843), pull(843)], paged(files))
    assert capsys.readouterr().out.strip() == HEAD
    assert len(calls) == 3


@pytest.mark.parametrize("files", [
    removed(842),
    removed(842) + removed(1),
    removed(843) + removed(1, 900),
])
def test_incomplete_or_duplicated_listing_fails_closed(monkeypatch, capsys, files):
    with pytest.raises(SystemExit, match="1"):
        execute(monkeypatch, [pull(843), pull(843)], paged(files))
    output = capsys.readouterr()
    assert "Cannot verify the changed paths" in output.err
    assert HEAD not in output.out


@pytest.mark.parametrize("after", [pull(843, "b" * 40), pull(844)])
def test_head_or_count_moving_during_listing_fails_closed(monkeypatch, capsys, after):
    with pytest.raises(SystemExit, match="1"):
        execute(monkeypatch, [pull(843), after], paged(removed(843)))
    assert "ValueError" in capsys.readouterr().err


@pytest.mark.parametrize("count", [0, 3000, 3500])
def test_counts_outside_the_listable_range_fail_before_listing(monkeypatch, capsys, count):
    with pytest.raises(SystemExit, match="1"):
        execute(monkeypatch, [pull(count)], [])
    assert "Cannot verify the changed paths" in capsys.readouterr().err


@pytest.mark.parametrize("entry", [
    {"filename": "tools/promote_release.py", "status": "removed"},
    {"filename": ".changefiles/new.json", "status": "added"},
    {"filename": ".changefiles/edit.json", "status": "modified"},
    {"filename": ".changefiles/moved.json", "status": "removed",
     "previous_filename": ".github/workflows/ci.yml"},
])
def test_any_change_other_than_a_changefile_removal_refuses_merge(monkeypatch, capsys, entry):
    files = removed(400) + [entry]
    with pytest.raises(SystemExit, match="1"):
        execute(monkeypatch, [pull(401), pull(401)], paged(files))
    output = capsys.readouterr()
    assert "refusing to admin-merge" in output.err
    assert entry["filename"] in output.err
    assert HEAD not in output.out


@pytest.mark.parametrize("change", [
    {"state": "closed"},
    {"base": {"ref": "main", "repo": {"full_name": REPOSITORY}}},
    {"base": {"ref": "dev", "repo": {"full_name": "fork/project"}}},
    {"head": {"sha": "short"}},
    {"changed_files": "843"},
])
def test_unexpected_pull_request_metadata_fails_closed(monkeypatch, capsys, change):
    with pytest.raises(SystemExit, match="1"):
        execute(monkeypatch, [pull(843, **change), pull(843)], paged(removed(843)))
    assert "Cannot verify the changed paths" in capsys.readouterr().err


@pytest.mark.parametrize("pages", [{}, [None], [[None]], [[{"filename": 1, "status": "removed"}]]])
def test_malformed_listing_fails_closed(monkeypatch, capsys, pages):
    with pytest.raises(SystemExit, match="1"):
        execute(monkeypatch, [pull(1), pull(1)], pages)
    assert "Cannot verify the changed paths" in capsys.readouterr().err


@pytest.mark.parametrize("error", [
    subprocess.CalledProcessError(1, ["gh"], stderr="HTTP 406 private provider details"),
    subprocess.TimeoutExpired(["gh"], 120),
    OSError("private local details"),
])
def test_provider_errors_fail_without_leaking_raw_output(monkeypatch, capsys, error):
    with pytest.raises(SystemExit, match="1"):
        execute(monkeypatch, [], [], error=error)
    output = capsys.readouterr()
    assert "refusing to admin-merge" in output.err
    assert "private" not in output.err + output.out


def test_invalid_pull_request_number_is_refused_without_provider_calls(monkeypatch):
    with pytest.raises(SystemExit, match="1"):
        assert execute(monkeypatch, [], [], number="7; rm") == []


def test_workflow_binds_both_checks_and_merge_to_the_verified_listing():
    run = run_script()
    commands = [line for line in run.splitlines() if not line.lstrip().startswith("#")]
    assert not any("gh pr diff" in line for line in commands)
    assert "max_removals=1000" in run
    assert '[ "$removed" -lt "$max_removals" ] || break' in run
    definition = run.index("verify_cleanup_paths() {")
    first = run.index('verify_cleanup_paths "$pr_number" >/dev/null')
    final = run.index('final_head=$(verify_cleanup_paths "$pr_number")')
    watch = run.index('gh pr checks "$pr_number" --watch --required')
    merge = run.index('--match-head-commit "$final_head"')
    assert definition < run.index("git push") < first < watch < final < merge
