"""An artifact-readiness retry must reuse, never replace, its published candidate."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import promote_release as promotion
from promotion_candidate import CandidateIdentityError, candidate_clock, candidate_environment, push_candidate
from test_promote_release import (
    _commit, _git, no_ambient_git_identity as _identity_fixture, repo as _repo_fixture,
)

repo = _repo_fixture
no_ambient_git_identity = _identity_fixture


@pytest.mark.parametrize("changed", ["none", "source", "state"])
def test_native_candidate_retry_preserves_exact_ref_and_refuses_new_source(
    tmp_path, repo, monkeypatch, changed,
):
    origin = tmp_path / "origin.git"
    _git(["init", "-q", "--bare", str(origin)], tmp_path)
    _git(["remote", "add", "origin", str(origin)], repo)
    _git(["push", "-q", "origin", "main", "dev"], repo)
    _git(["checkout", "-q", "dev"], repo)
    (repo / "README.md").write_text("first candidate\n")
    _commit(repo, "first source")
    _git(["push", "-q", "origin", "dev"], repo)
    _git(["checkout", "-q", "main"], repo)
    _git(["fetch", "-q", "origin"], repo)
    kwargs = dict(
        repo=repo, dev_ref="origin/dev", main_ref="origin/main",
        push=True, candidate_branch="release/promote-fixture",
        promotion_time="2026-10-14T10:00:00Z",
    )
    first = promotion.promote(**kwargs)
    expected_remote = first["commit"]
    if changed == "source":
        _git(["checkout", "-q", "dev"], repo)
        (repo / "README.md").write_text("different source\n")
        _commit(repo, "different source")
        _git(["push", "-q", "origin", "dev"], repo)
        _git(["checkout", "-q", "main"], repo)
        _git(["fetch", "-q", "origin"], repo)
    elif changed == "state":
        _git(["checkout", "-q", "--detach", first["commit"]], repo)
        state_path = repo / promotion.PIPELINE_STATE_PATH
        state = json.loads(state_path.read_text())
        state["last_promotion"].update(
            tag="promote-20350102030405", promoted_at="2035-01-02T03:04:05Z",
        )
        state_path.write_text(json.dumps(state))
        _git(["add", promotion.PIPELINE_STATE_PATH], repo)
        tree = _git(["write-tree"], repo)
        forged = _git(["commit-tree", tree, "-p", first["main_before"], "-m", "preseeded state"], repo)
        _git(["push", "-q", "--force", "origin", f"{forged}:refs/heads/release/promote-fixture"], repo)
        _git(["checkout", "-q", "--detach", forged], repo)
        _git(["checkout", "-q", "main"], repo)
        expected_remote = forged
    future = time.struct_time((2030, 1, 2, 3, 4, 5, 2, 2, 0))
    monkeypatch.setattr(promotion.time, "gmtime", lambda: future)
    if changed != "none":
        with pytest.raises(promotion.PromotionError, match="recomputed candidate identity"):
            promotion.promote(**kwargs)
    else:
        second = promotion.promote(**kwargs)
        assert second["commit"] == first["commit"]
        assert second["tag_stem"] == first["tag_stem"]
    remote = _git(
        ["ls-remote", "origin", "refs/heads/release/promote-fixture"], repo,
    ).split()[0]
    assert remote == expected_remote
    assert _git(["ls-remote", "origin", "refs/heads/main"], repo).split()[0] == first["main_before"]


@pytest.mark.parametrize("value", ["", "not-a-time", "2026-01-01T00:00:00+00:00", "2026-99-99T00:00:00Z"])
def test_immutable_clock_refuses_invalid_or_noncanonical_inputs(value):
    with pytest.raises(CandidateIdentityError, match="preparation time"):
        candidate_clock(value)


def test_fixed_clock_and_git_identity_do_not_mutate_parent_environment(monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "different caller")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "different clock")
    value = "2026-10-14T10:00:00Z"
    assert candidate_clock(value) == (value, "20261014100000")
    env = candidate_environment(value)
    assert env["GIT_AUTHOR_DATE"] == env["GIT_COMMITTER_DATE"] == value
    assert env["GIT_AUTHOR_NAME"] == env["GIT_COMMITTER_NAME"] == "github-actions[bot]"
    assert os.environ["GIT_AUTHOR_NAME"] == "different caller"
    assert os.environ["GIT_COMMITTER_DATE"] == "different clock"


@pytest.mark.parametrize("remote", ["same", "wrong-sha", "wrong-ref", "multiple", "absent"])
def test_retry_compares_the_full_commit_identity_without_fetching_or_normalizing(remote):
    commit, ref = "c" * 40, "refs/heads/release/promote-fixture"
    responses = {
        "same": f"{commit}\t{ref}", "wrong-sha": f"{'d' * 40}\t{ref}",
        "wrong-ref": f"{commit}\trefs/heads/other",
        "multiple": f"{commit}\t{ref}\n{'d' * 40}\t{ref}", "absent": "",
    }
    calls = []

    def git(argv, **kwargs):
        calls.append(argv)
        return responses[remote] if argv[0] == "ls-remote" else ""

    kwargs = dict(cwd=Path("."), git=git)
    if remote in ("same", "absent"):
        push_candidate(commit, "release/promote-fixture", **kwargs)
    else:
        with pytest.raises(CandidateIdentityError):
            push_candidate(commit, "release/promote-fixture", **kwargs)
    assert len(calls) == (2 if remote == "absent" else 1)
    assert not any(argv[0] == "fetch" for argv in calls)


def test_cli_emits_the_trusted_commit_directly_not_via_human_log_parsing(tmp_path, monkeypatch):
    commit = "c" * 40
    report = {
        "promoted": True, "commit": commit, "tag": None, "tag_stem": "promote-fixture",
        "main_before": "a" * 40, "dev_head": "b" * 40, "pushed": True,
        "candidate_branch": "release/promote-fixture",
        "bumps": {"untrusted\ncandidate_sha=" + "d" * 40: ("1", "2")},
    }
    monkeypatch.setattr(promotion, "promote", lambda **kwargs: report)
    output = tmp_path / "github-output"
    output.write_text("prior=value\n")
    assert promotion.main([
        "--push", "--candidate-branch", report["candidate_branch"],
        "--github-output", str(output),
    ]) == 0
    assert output.read_text() == f"prior=value\ncandidate_sha={commit}\n"


@pytest.mark.parametrize("paused", [False, True])
def test_cli_noop_never_emits_a_candidate_identity(tmp_path, monkeypatch, paused):
    def no_action(**kwargs):
        if paused:
            raise promotion.PromotionPaused("paused by authority")
        return {"promoted": False, "reason": "no source change"}

    monkeypatch.setattr(promotion, "promote", no_action)
    output = tmp_path / "github-output"
    assert promotion.main([
        "--push", "--candidate-branch", "release/promote-fixture",
        "--github-output", str(output),
    ]) == 0
    assert not output.exists()
