"""Exercise the trusted inline cleanup admission before any publication."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from _promotion_workflow_test_support import heredoc, source_workflow

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "owner/project"


def cleanup():
    return source_workflow(ROOT, "purge-consumed-changefiles.yml")


def script():
    steps = cleanup()["jobs"]["purge"]["steps"]
    run = next(step["run"] for step in steps if "cleanup_branch=" in step.get("run", ""))
    return heredoc(run)


def pull(number=7, run="123"):
    return {
        "number": number, "state": "open",
        "base": {"ref": "dev", "repo": {"full_name": REPOSITORY}},
        "head": {"ref": f"chore/purge-changefiles-{run}",
                 "repo": {"full_name": REPOSITORY}},
        "user": {"login": "owner"},
        "body": (
            "Generated cleanup.\n\n"
            f"<!-- agent-worktrees:source workflow=purge-consumed-changefiles run={run} -->"
        ),
    }


def execute(monkeypatch, pages, error=None):
    body = script()
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if error:
            raise error
        return SimpleNamespace(stdout=json.dumps(pages))

    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    monkeypatch.setattr(subprocess, "run", run)
    exec(compile(body, "<trusted-cleanup-guard>", "exec"), {})
    return calls


def test_complete_paginated_empty_result_allows_existing_publication(monkeypatch):
    calls = execute(monkeypatch, [[]])
    assert calls == [(
        ["gh", "api", "--paginate", "--slurp",
         "repos/owner/project/pulls?state=open&base=dev&per_page=100"],
        {"check": True, "capture_output": True, "text": True, "timeout": 45},
    )]


def test_later_page_pending_draft_or_hold_fails_with_all_links(monkeypatch, capsys):
    first, second = pull(), pull(9, "456")
    first["draft"] = True
    second["labels"] = [{"name": "hold"}]
    with pytest.raises(SystemExit, match="1"):
        execute(monkeypatch, [[pull(6, "12")], [first, second]])
    error = capsys.readouterr().err
    for number in (6, 7, 9):
        assert f"https://github.com/{REPOSITORY}/pull/{number}" in error
    assert "existing cleanup owner" in error
    assert "without changing their merge gates" in error


@pytest.mark.parametrize("change", [
    {"user": {"login": "contributor"}},
    {"head": {"ref": "chore/purge-changefiles-123", "repo": {"full_name": "fork/project"}}},
    {"head": {"ref": "chore/purge-changefiles-123", "repo": None}},
    {"head": {"ref": "pr/ordinary", "repo": {"full_name": REPOSITORY}}},
    {"body": "<!-- agent-worktrees:source workflow=other run=123 -->"},
    {"body": "<!-- agent-worktrees:source workflow=purge-consumed-changefiles run=456 -->"},
    {"body": None},
])
def test_unrelated_pr_is_not_taken_over(monkeypatch, change):
    pr = pull()
    pr.update(change)
    execute(monkeypatch, [[pr]])


def test_repository_and_owner_matching_is_case_insensitive(monkeypatch):
    pr = pull()
    pr["user"]["login"] = "OwNeR"
    pr["head"]["repo"]["full_name"] = "OWNER/PROJECT"
    pr["base"]["repo"]["full_name"] = "OWNER/PROJECT"
    with pytest.raises(SystemExit, match="1"):
        execute(monkeypatch, [[pr]])


@pytest.mark.parametrize("pages", [
    [], {}, [None], [[None]], [[{}]],
    [[{**pull(), "number": True}]],
    [[{**pull(), "state": "closed"}]],
    [[{**pull(), "base": {"ref": "main", "repo": {"full_name": REPOSITORY}}}]],
    [[{**pull(), "head": {"ref": 123, "repo": None}}]],
    [[{**pull(), "body": 123}]],
])
def test_unknown_or_malformed_provider_metadata_fails_closed(monkeypatch, capsys, pages):
    with pytest.raises(SystemExit, match="1"):
        execute(monkeypatch, deepcopy(pages))
    assert "Cannot verify outstanding cleanup PRs" in capsys.readouterr().err


@pytest.mark.parametrize("error", [
    subprocess.CalledProcessError(1, ["gh"], stderr="private provider details"),
    subprocess.TimeoutExpired(["gh"], 45),
    OSError("private local details"),
])
def test_provider_errors_fail_without_leaking_raw_output(monkeypatch, capsys, error):
    with pytest.raises(SystemExit, match="1"):
        execute(monkeypatch, [[]], error)
    output = capsys.readouterr()
    assert "refusing another publication" in output.err
    assert "private" not in output.err + output.out


def test_non_json_provider_output_fails_closed(monkeypatch, capsys):
    body = script()
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="not JSON"))
    with pytest.raises(SystemExit, match="1"):
        exec(compile(body, "<trusted-cleanup-guard>", "exec"), {})
    assert "JSONDecodeError" in capsys.readouterr().err


def test_guard_precedes_commit_push_and_preserves_existing_safety():
    workflow = cleanup()
    job = workflow["jobs"]["purge"]
    run = job["steps"][-1]["run"]
    assert run.index("staged_paths=") < run.index("python3 - <<'PY'")
    assert run.index("\nPY\n") < run.index("git commit") < run.index("git push")
    assert workflow["concurrency"] == {
        "group": "purge-consumed-changefiles", "cancel-in-progress": False,
    }
    assert job["timeout-minutes"] == 10
    assert "deadline=$(( $(date +%s) + 360 ))" in run
    assert '--match-head-commit "$final_head"' in run
    assert 'gh pr checks "$pr_number" --watch --required' in run
